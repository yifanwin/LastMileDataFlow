"""Single-device inference does not require training activation sharding."""

def activation_sharding_constraint(value):
    return value
