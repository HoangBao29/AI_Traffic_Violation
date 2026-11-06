import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from train import dataset_metadata, dataset_summary, load_dataset


class TrainingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dataset_dir = self.root / "dataset"
        self.config_file = self.dataset_dir / "data.yaml"
        self.config = {
            "train": "train/images",
            "val": "valid/images",
            "test": "test/images",
            "names": ["With Helmet", "Without Helmet"],
            "nc": 2,
            "roboflow": {
                "url": "https://example.test/dataset",
                "license": "CC BY 4.0",
            },
        }
        self.dataset_dir.mkdir()
        self.config_file.write_text(yaml.safe_dump(self.config), encoding="utf-8")
        for split in ("train", "valid", "test"):
            images_dir = self.dataset_dir / split / "images"
            labels_dir = self.dataset_dir / split / "labels"
            images_dir.mkdir(parents=True)
            labels_dir.mkdir()
            (images_dir / "frame.jpg").touch()
            (labels_dir / "frame.txt").write_text("", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dataset_summary_deduplicates_identical_annotations(self):
        image_path = self.dataset_dir / "test" / "images" / "frame.jpg"
        image_path.touch()
        label_path = self.dataset_dir / "test" / "labels" / "frame.txt"
        label_path.write_text(
            "1 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n",
            encoding="utf-8",
        )
        _, config, root, class_names = load_dataset(self.config_file)

        summary = dataset_summary(config, root, class_names, include_test=True)
        test_split = summary["splits"]["test"]

        self.assertEqual(test_split["total_annotations"], 1)
        self.assertEqual(test_split["duplicate_annotations"], 1)
        self.assertEqual(test_split["annotations_per_class"]["Without Helmet"], 1)

    def test_dataset_summary_rejects_missing_label_files(self):
        (self.dataset_dir / "test" / "labels" / "frame.txt").unlink()
        _, config, root, class_names = load_dataset(self.config_file)

        with self.assertRaisesRegex(ValueError, "without matching label files"):
            dataset_summary(config, root, class_names, include_test=True)

    def test_dataset_metadata_reads_nested_roboflow_attribution(self):
        self.assertEqual(
            dataset_metadata(self.config),
            {
                "source": "https://example.test/dataset",
                "license": "CC BY 4.0",
            },
        )


if __name__ == "__main__":
    unittest.main()
