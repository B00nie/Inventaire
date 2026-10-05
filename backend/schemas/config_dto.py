from dataclasses import dataclass

@dataclass
class ConfigDTO:
    source_dir: str
    target_dir: str
    classes: list = None

    @classmethod
    def from_dict(cls, data: dict) -> 'ConfigDTO':
        if not isinstance(data, dict):
            raise ValueError("Input data must be a dictionary.")

        source_dir = data.get('source_dir')
        target_dir = data.get('target_dir')

        if source_dir is None or not isinstance(source_dir, str) or len(source_dir) == 0:
            raise ValueError("Invalid or missing 'source_dir' field. It must be a non-empty string.")

        if target_dir is None or not isinstance(target_dir, str) or len(target_dir) == 0:
            raise ValueError("Invalid or missing 'target_dir' field. It must be a non-empty string.")

        if 'classes' in data:
            classes = data['classes']
            if not isinstance(classes, list) or not all(isinstance(cls_name, str) for cls_name in classes):
                raise ValueError("Invalid 'classes' field. It must be a list of strings.")

        return cls(
            source_dir=source_dir,
            target_dir=target_dir,
            classes=classes
        )