from setuptools import find_packages, setup

setup(
    name="receipt-dynamo",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["requests", "Pillow", "boto3>=1.26.0"],
    extras_require={
        "test": [
            "pytest",
            "pytest-mock",
            "pytest-cov",
            "moto",
            "freezegun",
        ]
    },
)
