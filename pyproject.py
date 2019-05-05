import os

from pathlib import Path

from poetry.masonry.api import (
    build_sdist,
    build_wheel,
    get_requires_for_build_wheel,
    get_requires_for_build_sdist,
    prepare_metadata_for_build_wheel as _prepare_metadata_for_build_wheel,
)
from poetry.poetry import Poetry


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    name = _prepare_metadata_for_build_wheel(metadata_directory, config_settings)

    if os.environ.get('READTHEDOCS') != 'True':
        return name

    poetry = Poetry.create(".")

    metadata = Path(metadata_directory) / name / 'METADATA'

    text = metadata.read_text(encoding='utf-8')

    try:
        # Start of long description.
        index = text.index('\n\n')
    except ValueError:
        index = len(text)

    dev_requires = '\n'.join(
        f'Requires-Dist: {d.to_pep_508()}'
        for d in poetry.package.dev_requires
    )

    new_text = f'{text[:index]}\n{dev_requires}{text[index:]}'

    metadata.write_text(new_text, encoding='utf-8')

    return name
