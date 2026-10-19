"""Language-only task prompts shared by policy rollout and training data."""
import re


def instruction(task):
    name = re.sub(r'(?<!^)(?=[A-Z])', ' ', task.get('category') or 'object').lower()
    if task['operation'] == 'pick':
        return f'Pick up the {name}.'
    part = 'drawer' if task['joint_kind'] == 'slide' else 'door'
    return f'Open the {part} of the {name}.'
