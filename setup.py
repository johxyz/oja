from setuptools import setup

setup(
    name="oja",
    version="1.0.2",
    py_modules=["automation"],
    install_requires=[
        "requests>=2.32.0",
        "PyMuPDF>=1.23.0", 
        "beautifulsoup4>=4.12.0"
    ],
    entry_points={
        "console_scripts": [
            "oja=automation:main",
        ],
    },
) 