"""Exercise mobile RL controls and failure detection on real assets and GPU."""

import argparse
import json
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import read_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    select_devices([args.gpu])
    # Bind CUDA/EGL before importing torch, MuJoCo and mjlab.
    import torch
    from lastmile_dataflow.station_map.rl.config import resolve_config
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv
    from lastmile_dataflow.station_map.rl.evaluation import reset_stations

    config = resolve_config(read_config(args.config))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "config.json").write_text(json.dumps(config, indent=2))
    env = StationEnv(config, gpu=0, seed=42, num_envs=2)
    result = {"gpu": args.gpu, "controls": []}
    try:
        pool = env.task.pool.tolist()
        # Central valid station; start all control checks from the same Home state.
        anchor = env.prepared.target_position
        station = min(pool, key=lambda i: (env.task.stations[i]["x"] - anchor[0]) ** 2
                      + (env.task.stations[i]["y"] - anchor[1]) ** 2)
        result["station"] = env.task.stations[station]
        result["stations"] = {"total": len(env.task.stations), "valid": len(pool)}
        for dimension, label in ((7, "forward"), (8, "side"), (9, "yaw"), (2, "tcp_z")):
            for sign in (1, -1):
                obs = reset_stations(env, [station] * 2, mode="flat")
                assert obs.shape == (2, 75) and torch.isfinite(obs).all()
                before = env.task.fixed_state().clone()
                tcp_before = env.task.poses()[0].clone()
                local_before = torch.einsum("bji,bj->bi", env.task.poses()[3], tcp_before - env.task.poses()[2])
                action = torch.zeros(2, 10, device=env.device)
                action[:, 6] = -1
                action[:, dimension] = sign
                max_command_step = 0.
                max_command_yaw = 0.
                for _ in range(20):
                    measured = env.task.robot.data.joint_pos[:, env.task.base_joints].clone()
                    observations, _, terminated, truncated, _ = env.step(action)
                    assert torch.isfinite(observations["policy"]["flat"]).all()
                    lead = env.action_manager.get_term("base").command - measured
                    max_command_step = max(max_command_step, float(lead[:, :2].norm(dim=-1).max()))
                    max_command_yaw = max(max_command_yaw, float(lead[:, 2].abs().max()))
                    assert not env.task.unhealthy.any()
                    if terminated.any() or truncated.any():
                        break
                after = env.task.fixed_state().clone()
                rotation = env.task.poses()[3]
                local_after = torch.einsum("bji,bj->bi", rotation, env.task.poses()[0] - env.task.poses()[2])
                delta = after - before
                yaw = before[:, 2]
                world_delta = delta[:, :2]
                body_delta = torch.stack((yaw.cos() * world_delta[:, 0] + yaw.sin() * world_delta[:, 1],
                                          -yaw.sin() * world_delta[:, 0] + yaw.cos() * world_delta[:, 1]), -1)
                row = {"control": label, "sign": sign, "delta_xyyaw_lift": delta.cpu().tolist(),
                       "delta_body_xy": body_delta.cpu().tolist(),
                       "tcp_local_change_m": (local_after - local_before).norm(dim=-1).cpu().tolist(),
                       "max_command_step_m": max_command_step, "max_command_yaw_rad": max_command_yaw,
                       "max_measured_speed_m_s": float(env.task.max_base_speed.max()),
                       "max_measured_yaw_rad_s": float(env.task.max_base_yaw_speed.max()),
                       "collision": (~env.task.collision_free).cpu().tolist(),
                       "out_of_bounds": env.task.out_of_bounds.cpu().tolist()}
                result["controls"].append(row)
                cfg = config["action"]
                assert max_command_step <= cfg["base_translation_m"] + 1e-5
                assert max_command_yaw <= cfg["base_yaw_rad"] + 1e-5
                if label in ("forward", "side"):
                    assert float(body_delta[:, 0 if label == "forward" else 1].mean()) * sign > .005
                elif label == "yaw":
                    assert float(delta[:, 2].mean()) * sign > .01
                else:
                    assert float(delta[:, 3].mean()) * sign > .00001
        reset_stations(env, [station] * 2, mode="flat")
        # Move the first lane, then prove resetting only lane 1 preserves lane 0.
        action = torch.zeros(2, 10, device=env.device)
        action[:, 6] = -1
        action[0, 7] = 1
        for _ in range(5):
            env.step(action)
        state = env.sim.data.qpos[0].clone()
        command = env.action_manager.get_term("base").command[0].clone()
        reset_stations(env, [station], env_ids=torch.tensor([1], device=env.device), mode="flat")
        torch.testing.assert_close(env.sim.data.qpos[0], state)
        torch.testing.assert_close(env.action_manager.get_term("base").command[0], command)
        result["partial_reset_isolated"] = True
        # Inject an out-of-region physical state solely to test the failure path.
        reset_stations(env, [station] * 2, mode="flat")
        q = env.task.robot.data.joint_pos.clone()
        q[:, env.task.base_joints[0]] = float(anchor[0]) + 3
        env.task.robot.write_joint_state_to_sim(q, torch.zeros_like(q))
        env.sim.forward()
        _, _, terminated, _, _ = env.step(torch.zeros(2, 10, device=env.device))
        assert env.task.out_of_bounds.all() and terminated.all() and not env.task.success.any()
        result["boundary_terminal_without_success"] = True
        result["passed"] = True
    finally:
        (args.output / "smoke.json").write_text(json.dumps(result, indent=2))
        env.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
