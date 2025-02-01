from setuptools import setup, find_packages

setup(
    name="dynamo",
    version="1",
    packages=find_packages(),
    install_requires=["requests", "Pillow"],
    extras_require={
        "test": [
            "pytest",
            "pytest-mock",
            "pytest-cov",
            "moto",
            "pytest-cov",
            "freezegun",
        ]
    },
)
