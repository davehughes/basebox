#!/usr/bin/python
import setuptools
setuptools.setup(
    name='basebox',
    version='0.1.2',
    packages=setuptools.find_packages(),
    install_requires=['fabric', 'cuisine', 'proxytypes'],
    entry_points={
        'console_scripts': [
            'basebox = basebox.cli:main'
            ]
        }
)
