"""π0.5 训练数据准备：把采集或标定得到的 RBY-1 成功示范转成 LeRobot v2.1 数据和归一化统计，不运行仿真和训练。"""

import argparse
from pathlib import Path

from lastmile_dataflow.lastmile_dataset.pi05_export import convert, prepare_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path, help="已完成的 LastMile 数据集目录（含 train_index.jsonl）")
    source.add_argument("--evaluations", type=Path, nargs="+", help="标定 run 目录，取其中完整的成功试验")
    parser.add_argument(
        "--output", type=Path, required=True, help="新输出目录；写 reference/（仅 --evaluations）和 lerobot/"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    dataset = args.dataset
    if args.evaluations:
        dataset = prepare_reference(args.evaluations, args.output / "reference")
    result = convert(dataset, args.output / "lerobot")
    print(result)


if __name__ == "__main__":
    main()
