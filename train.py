"""Train and evaluate the helmet detector with reproducible run artifacts."""

import argparse
import json
import platform
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
import ultralytics
import yaml
from ultralytics import YOLO


ROOT_DIR = Path(__file__).resolve().parent
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Train the helmet detector and save evaluation metrics."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=ROOT_DIR / "datasets" / "helmet" / "data.yaml",
        help="Ultralytics dataset YAML file.",
    )
    parser.add_argument(
        "--model",
        default=str(ROOT_DIR / "yolov8n.pt"),
        help="Initial weights or an Ultralytics model name.",
    )
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--device",
        default=None,
        help="Training device, for example 0, 0,1, or cpu. Defaults to available CUDA or CPU.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=ROOT_DIR / "runs" / "detect",
        help="Directory for Ultralytics run artifacts.",
    )
    parser.add_argument("--name", default="helmet")
    parser.add_argument(
        "--evaluate-test",
        action="store_true",
        help="Evaluate the held-out test split after training; use for final model reporting.",
    )
    parser.add_argument(
        "--evaluate-only",
        action="store_true",
        help="Evaluate an existing checkpoint from --model without training.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Check dataset files and print split/class counts without training.",
    )
    args = parser.parse_args(argv)
    if args.epochs < 1 or args.imgsz < 1 or args.batch < 1:
        parser.error("--epochs, --imgsz, and --batch must be positive integers.")
    if args.workers < 0 or args.patience < 0:
        parser.error("--workers and --patience cannot be negative.")
    if args.validate_only and args.evaluate_only:
        parser.error("--validate-only and --evaluate-only cannot be used together.")
    return args


def load_dataset(data_file):
    data_file = data_file.expanduser().resolve()
    if not data_file.is_file():
        raise FileNotFoundError(f"Dataset YAML was not found: {data_file}")

    with data_file.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError(f"Dataset YAML must contain a mapping: {data_file}")

    dataset_root = Path(config.get("path", data_file.parent))
    if not dataset_root.is_absolute():
        dataset_root = data_file.parent / dataset_root
    dataset_root = dataset_root.resolve()

    for split in ("train", "val"):
        if not config.get(split):
            raise ValueError(f"Dataset YAML is missing the required '{split}' split.")
    if not isinstance(config.get("names"), (list, dict)):
        raise ValueError("Dataset YAML must define class names as a list or mapping.")

    names = config["names"]
    class_names = (
        {int(class_id): str(name) for class_id, name in names.items()}
        if isinstance(names, dict)
        else {class_id: str(name) for class_id, name in enumerate(names)}
    )
    declared_count = int(config.get("nc", len(class_names)))
    if declared_count != len(class_names):
        raise ValueError(
            f"Dataset declares nc={declared_count} but defines {len(class_names)} class names."
        )

    return data_file, config, dataset_root, class_names


def dataset_metadata(config):
    roboflow = config.get("roboflow")
    if not isinstance(roboflow, dict):
        roboflow = {}
    return {
        "source": config.get("url") or roboflow.get("url"),
        "license": config.get("license") or roboflow.get("license"),
    }


def split_paths(config, dataset_root, split):
    configured_paths = config.get(split)
    if isinstance(configured_paths, (str, Path)):
        configured_paths = [configured_paths]
    if not isinstance(configured_paths, list) or not configured_paths:
        raise ValueError(f"Dataset split '{split}' must be a path or a list of paths.")

    resolved = []
    for configured_path in configured_paths:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = dataset_root / path
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Dataset split '{split}' path was not found: {path}")
        resolved.append(path)
    return resolved


def summarize_split(config, dataset_root, split, class_names):
    image_paths = []
    for path in split_paths(config, dataset_root, split):
        if path.is_dir():
            image_paths.extend(
                image for image in path.rglob("*")
                if image.is_file() and image.suffix.lower() in IMAGE_SUFFIXES
            )
        elif path.suffix.lower() in IMAGE_SUFFIXES:
            image_paths.append(path)
        else:
            raise ValueError(f"Dataset split '{split}' contains a non-image file path: {path}")

    image_paths.sort()
    if not image_paths:
        raise ValueError(f"Dataset split '{split}' has no supported images.")

    class_counts = Counter()
    missing_labels = 0
    empty_labels = 0
    duplicate_annotations = 0
    invalid_labels = []
    for image_path in image_paths:
        label_path = image_path.parent.parent / "labels" / f"{image_path.stem}.txt"
        if not label_path.is_file():
            missing_labels += 1
            continue

        label_rows = label_path.read_text(encoding="utf-8").splitlines()
        if not any(row.strip() for row in label_rows):
            empty_labels += 1
        seen_rows = set()
        for line_number, row in enumerate(label_rows, start=1):
            if not row.strip():
                continue
            if row in seen_rows:
                duplicate_annotations += 1
                continue
            seen_rows.add(row)
            parts = row.split()
            try:
                class_id = int(parts[0])
                coordinates = [float(value) for value in parts[1:]]
                valid_box = (
                    len(coordinates) == 4
                    and all(0 <= value <= 1 for value in coordinates)
                    and coordinates[2] > 0
                    and coordinates[3] > 0
                )
                if class_id not in class_names or not valid_box:
                    raise ValueError
            except (ValueError, IndexError):
                invalid_labels.append(f"{label_path}:{line_number}")
                continue
            class_counts[class_id] += 1

    if missing_labels or invalid_labels:
        examples = ", ".join(invalid_labels[:5])
        raise ValueError(
            f"Dataset split '{split}' has {missing_labels} images without matching "
            f"label files and {len(invalid_labels)} invalid label rows"
            f"{f' (examples: {examples})' if examples else ''}."
        )

    return {
        "images": len(image_paths),
        "label_files_missing": missing_labels,
        "empty_label_files": empty_labels,
        "duplicate_annotations": duplicate_annotations,
        "annotations_per_class": {
            class_names[class_id]: class_counts.get(class_id, 0)
            for class_id in sorted(class_names)
        },
        "total_annotations": sum(class_counts.values()),
    }


def dataset_summary(config, dataset_root, class_names, include_test):
    splits = ["train", "val"]
    if include_test:
        if not config.get("test"):
            raise ValueError("--evaluate-test was requested but the dataset YAML has no test split.")
        splits.append("test")
    return {
        "classes": class_names,
        "splits": {
            split: summarize_split(config, dataset_root, split, class_names)
            for split in splits
        },
    }


def metrics_to_json(metrics):
    result = {}
    for key, value in metrics.items():
        if hasattr(value, "item"):
            value = value.item()
        result[str(key)] = value if isinstance(value, (int, float, str, bool)) else str(value)
    return result


def device_summary(device):
    selected_device = device if device is not None else (0 if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available() and str(selected_device) != "cpu":
        device_name = torch.cuda.get_device_name(0)
    else:
        device_name = "CPU"
    return selected_device, device_name


def main(argv=None):
    args = parse_args(argv)
    data_file, config, dataset_root, class_names = load_dataset(args.data)
    summary = dataset_summary(
        config, dataset_root, class_names, include_test=args.evaluate_test
    )
    if args.validate_only:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    device, device_name = device_summary(args.device)
    if args.evaluate_only:
        best_weights = Path(args.model).expanduser().resolve()
        if not best_weights.is_file():
            raise FileNotFoundError(f"Checkpoint was not found: {best_weights}")
        run_name = f"{args.name}-evaluation-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
        run_dir = args.project.expanduser().resolve() / run_name
        run_dir.mkdir(parents=True, exist_ok=False)
    else:
        model = YOLO(args.model)
        model.train(
            data=str(data_file),
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=device,
            workers=args.workers,
            patience=args.patience,
            seed=args.seed,
            deterministic=True,
            plots=True,
            project=str(args.project.expanduser().resolve()),
            name=args.name,
            exist_ok=False,
        )

        run_dir = Path(model.trainer.save_dir).resolve()
        best_weights = run_dir / "weights" / "best.pt"
        if not best_weights.is_file():
            raise FileNotFoundError(
                f"Training did not produce the expected checkpoint: {best_weights}"
            )

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_type": "evaluation_only" if args.evaluate_only else "train_and_evaluate",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "ultralytics": ultralytics.__version__,
            "pytorch": torch.__version__,
            "device": str(device),
            "device_name": device_name,
        },
        "training": None if args.evaluate_only else {
            "initial_model": args.model,
            "best_weights": str(best_weights),
            "epochs_requested": args.epochs,
            "image_size": args.imgsz,
            "batch_size": args.batch,
            "workers": args.workers,
            "patience": args.patience,
            "seed": args.seed,
            "deterministic": True,
        },
        "dataset": {
            "yaml": str(data_file),
            **dataset_metadata(config),
            **summary,
        },
        "evaluation": {},
    }
    report["checkpoint"] = str(best_weights)

    for split in ("val", "test") if args.evaluate_test else ("val",):
        evaluation_model = YOLO(str(best_weights))
        results = evaluation_model.val(
            data=str(data_file),
            split=split,
            imgsz=args.imgsz,
            batch=args.batch,
            device=device,
            plots=True,
            project=str(run_dir / "evaluation"),
            name=split,
            exist_ok=True,
        )
        report["evaluation"][split] = {
            "metrics": metrics_to_json(results.results_dict),
            "speed_ms_per_image": metrics_to_json(results.speed),
        }

    report_path = run_dir / "metrics_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Best weights: {best_weights}")
    print(f"Metrics report: {report_path}")
    return 0


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    raise SystemExit(main())
