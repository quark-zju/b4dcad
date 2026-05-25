from setuptools import setup, find_packages

setup(
    name='b4dcad',
    version='0.1.1',
    url='https://github.com/wrongbad/badcad.git',
    author='quark',
    description='personal 3D printing CAD workflow built on Manifold',
    packages=find_packages(),    
    install_requires=[
        'manifold3d',
    ],
    entry_points={
        'console_scripts': [
            'b4dcad=b4dcad.cli:main',
            'b4dcad-stl=b4dcad.cli:stl_command',
            'b4dcad-preview=b4dcad.cli:preview_command',
        ],
    },
)
