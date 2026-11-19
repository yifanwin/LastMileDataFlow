"""Real-GPU checks for random target-facing stations and a physically fixed base."""

import argparse
import json
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import REPO_ROOT, read_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs/station_map/rl/val103_cube_random_stations_facing.yaml")
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--num-envs", type=int, default=16)
    parser.add_argument("--steps", type=int, default=256)
    args = parser.parse_args()
    select_devices([args.gpu])
    # MuJoCo/torch read device bindings during import.
    import torch
    from lastmile_dataflow.station_map.rl.config import resolve_config
    from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv
    from lastmile_dataflow.station_map.rl.env.scene import ROBOT_PREFIX
    from lastmile_dataflow.station_map.rl.sac.agent import seed_all

    config = resolve_config(read_config(args.config))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "config.json").write_text(json.dumps(config, indent=2))
    env = StationEnv(config, gpu=VISIBLE_DEVICE, seed=42, num_envs=args.num_envs)
    try:
        task = env.task
        assert task.physical_fixed and len(task.pool) > 1
        assert len(task.robot.joint_names) == 10
        assert not any("joint_mobile_" in name for name in task.robot.joint_names)
        assert not any("actuator_mobile_" in env.sim.mj_model.actuator(i).name for i in range(env.sim.mj_model.nu))
        seed_all(42)
        observation, _ = env.reset()
        assert observation["policy"]["flat"].shape == (args.num_envs, 69)
        first = task.station_ids.clone()
        seed_all(42)
        env.reset()
        torch.testing.assert_close(task.station_ids, first, rtol=0, atol=0)
        cube_start = int(env.sim.mj_model.joint("RL_Cube_free").qposadr[0])
        cube_slice = slice(cube_start, cube_start + 7)
        cube_initial = env.sim.data.qpos[:, cube_slice].clone()
        rounds = [first.tolist()]
        for _ in range(3):
            observation, _ = env.reset()
            rounds.append(task.station_ids.tolist())
            torch.testing.assert_close(env.sim.data.qpos[:, cube_slice], cube_initial, rtol=0, atol=0)
            expected = task.station_poses[task.station_ids]
            flat = observation["policy"]["flat"]
            torch.testing.assert_close(flat[:, 65:68], expected, rtol=0, atol=1e-6)
            torch.testing.assert_close(flat[:, 68:69], task.robot.data.joint_pos[:, task.lift])
        assert len({i for row in rounds for i in row}) > 1

        # Partial reset must not change any other environment's states or station.
        untouched = torch.arange(2, env.num_envs, device=env.device)
        qpos = env.sim.data.qpos[untouched].clone()
        mocap = env.sim.data.mocap_pos[untouched].clone()
        stations = task.station_ids[untouched].clone()
        env.reset(env_ids=torch.tensor([0, 1], device=env.device))
        torch.testing.assert_close(env.sim.data.qpos[untouched], qpos, rtol=0, atol=0)
        torch.testing.assert_close(env.sim.data.mocap_pos[untouched], mocap, rtol=0, atol=0)
        torch.testing.assert_close(task.station_ids[untouched], stations, rtol=0, atol=0)

        base_id = env.sim.mj_model.body(ROBOT_PREFIX + "mobilebase0_base").id
        force = torch.tensor([500., -500., 500., 100., 100., 100.], device=env.device)
        position = env.sim.data.site_xpos[:, task.base].clone()
        rotation = env.sim.data.site_xmat[:, task.base].clone()
        maximum_position_drift = maximum_rotation_drift = maximum_reference_error = 0.
        collision_steps = completed = 0
        arm_initial = task.robot.data.joint_pos[:, task.arm].clone()
        for _ in range(args.steps):
            env.sim.data.xfrc_applied[:, base_id] = force
            action = 2 * torch.rand(env.num_envs, 7, device=env.device) - 1
            observation, reward, terminated, truncated, _ = env.step(action)
            assert torch.isfinite(observation["policy"]["flat"]).all() and torch.isfinite(reward).all()
            assert not task.unhealthy.any()
            drift = (env.sim.data.site_xpos[:, task.base] - position).norm(dim=-1).max().item()
            rotation_drift = (env.sim.data.site_xmat[:, task.base] - rotation).abs().max().item()
            maximum_position_drift = max(maximum_position_drift, drift)
            maximum_rotation_drift = max(maximum_rotation_drift, rotation_drift)
            maximum_reference_error = max(maximum_reference_error, task.max_drift.max().item())
            collision_steps += int(task.step_collision.sum())
            done = terminated | truncated
            completed += int(done.sum())
            if done.any():
                env.reset(env_ids=done.nonzero(as_tuple=True)[0])
                position[done] = env.sim.data.site_xpos[done, task.base]
                rotation[done] = env.sim.data.site_xmat[done, task.base]
        assert maximum_position_drift <= 1e-6 and maximum_rotation_drift <= 1e-6
        assert maximum_reference_error <= 1e-6
        assert not task.control_failed.any()
        arm_motion = (task.robot.data.joint_pos[:, task.arm] - arm_initial).abs().max().item()
        assert arm_motion > 0
        result = {
            "physical_gpu": args.gpu, "num_envs": env.num_envs, "steps": args.steps,
            "candidate_stations": len(task.stations), "valid_stations": len(task.pool),
            "invalid_reasons": {reason: sum(s["invalid_reason"] == reason for s in task.stations)
                                for reason in sorted({s["invalid_reason"] for s in task.stations if not s["valid"]})},
            "observation_dim": 69, "station_draws": rounds,
            "max_position_drift_m": maximum_position_drift,
            "max_rotation_matrix_change": maximum_rotation_drift,
            "max_station_reference_error_m": maximum_reference_error,
            "max_arm_motion_rad": arm_motion, "collision_env_steps": collision_steps,
            "completed_episodes": completed, "applied_base_wrench": force.tolist(),
        }
        (args.output / "smoke.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        env.close()


if __name__ == "__main__":
    main()
