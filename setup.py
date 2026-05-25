from setuptools import setup, find_packages

setup(
    name='badcad',
    version='0.1.1',
    url='https://github.com/wrongbad/badcad.git',
    author='wrongbad',
    description='csg for python workflows',
    packages=find_packages(),    
    install_requires=[
        'manifold3d',
    ],
    entry_points={
        'console_scripts': [
            'badcad=badcad.cli:main',
            'badcad-stl=badcad.cli:stl_command',
            'badcad-preview=badcad.cli:preview_command',
        ],
    },
)
