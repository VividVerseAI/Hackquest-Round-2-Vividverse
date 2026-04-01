from setuptools import setup

# Install from the repo root: pip install -e .
# This maps "import vividverse" to the Subnet/ directory.

setup(
    name="vividverse",
    version="0.1.0",
    description="Vividverse subnet mechanism",
    package_dir={
        "vividverse":           "Subnet",
        "vividverse.contracts": "Subnet/contracts",
        "vividverse.utils":     "Subnet/utils",
        "vividverse.validator": "Subnet/validator",
    },
    packages=[
        "vividverse",
        "vividverse.contracts",
        "vividverse.utils",
        "vividverse.validator",
    ],
    install_requires=[
        "bittensor>=7.0.0",
        "torch",
        "requests",
    ],
)

