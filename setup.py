from setuptools import find_packages, setup


setup(
    entry_points={
        'console_scripts': ['example=example.main:main'],
    },
    name='example',
    packages=find_packages(),
    version='0.1.0.dev0',
)
