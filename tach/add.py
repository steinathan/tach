import os
from datetime import datetime
from typing import Iterable, Optional

import yaml

from tach import filesystem as fs
from tach.check import check
from tach.colors import BCOLORS
from tach.constants import CONFIG_FILE_NAME, PACKAGE_FILE_NAME
from tach.core import ScopeDependencyRules
from tach.errors import TachError
from tach.filesystem import canonical
from tach.parsing import parse_project_config, parse_package_config

init_content_template = """# Generated by tach on {timestamp}
from .main import *
"""


def build_init_content():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return init_content_template.format(timestamp=timestamp)


def build_package(path: str, tags: Optional[set[str]]) -> Optional[str]:
    dirname = path.removesuffix(".py")
    new_tag = canonical(dirname).replace(os.path.sep, ".")
    if not tags:
        tags_to_write = [new_tag]
    else:
        tags_to_write = tags
    if os.path.isfile(path):
        # Create the package directory
        os.mkdir(dirname)
        # Write the __init__
        fs.write_file(os.path.join(dirname, "__init__.py"), build_init_content())
        # Move and rename the file
        os.rename(path, os.path.join(dirname, "main.py"))
    # Write the package.yml
    comma_separated_tags = ",".join(map(lambda tag: f'"{tag}"', tags_to_write))
    package_yml_content = f"tags: [{comma_separated_tags}]\n"
    fs.write_file(
        os.path.join(dirname, f"{PACKAGE_FILE_NAME}.yml"), package_yml_content
    )

    if not tags:
        return new_tag


def update_project_config(root: str, tags: set[str]):
    current_dir = fs.get_cwd()
    try:
        fs.chdir(root)
        project_config = parse_project_config()
        check_errors = check(
            root,
            project_config=project_config,
            exclude_paths=project_config.exclude,
        )
        # TODO: handle case where we pivoted a file in a strict package
        for error in check_errors:
            if error.is_tag_error:
                invalid_tags = set(error.invalid_tags)
                existing_dependencies = set(
                    project_config.constraints.get(
                        error.source_tag, ScopeDependencyRules(depends_on=[])
                    ).depends_on
                )
                if error.source_tag in tags:
                    # This is updating the config for a new tag
                    project_config.constraints[error.source_tag] = ScopeDependencyRules(
                        depends_on=list(existing_dependencies | invalid_tags)
                    )
                if invalid_tags & tags:
                    # This is updating the config for an existing tag
                    project_config.constraints[error.source_tag] = ScopeDependencyRules(
                        depends_on=list(existing_dependencies | (invalid_tags & tags))
                    )

        tach_yml_path = os.path.join(root, f"{CONFIG_FILE_NAME}.yml")
        tach_yml_content = yaml.dump(project_config.model_dump())
        fs.write_file(tach_yml_path, tach_yml_content)

        check_errors = check(
            root, project_config=project_config, exclude_paths=project_config.exclude
        )
        if check_errors:
            return (
                "Could not auto-detect all dependencies, "
                "use 'tach check' to finish initialization manually."
            )
    except Exception as e:
        fs.chdir(current_dir)
        raise e
    fs.chdir(current_dir)


def validate_path(path: str) -> None:
    if not os.path.exists(path):
        raise TachError(f"{path} does not exist.")
    if os.path.isdir(path):
        # 'path' points to a directory
        # so we validate that it is a Python package without an existing package config
        if os.path.exists(
            os.path.join(path, f"{PACKAGE_FILE_NAME}.yml")
        ) or os.path.exists(os.path.join(path, f"{PACKAGE_FILE_NAME}.yaml")):
            raise TachError(f"{path} already contains a {PACKAGE_FILE_NAME}.yml")
        if not os.path.exists(os.path.join(path, "__init__.py")):
            raise TachError(
                f"{path} is not a valid Python package (no __init__.py found)."
            )
    else:
        # 'path' points to a file
        # so we validate that it is a Python file we can 'pivot' to a package
        print(
            f"{BCOLORS.WARNING}'{path}' will be moved into a new package. "
            f"You may need to update relative imports within this file.{BCOLORS.ENDC}"
        )
        if not path.endswith(".py"):
            raise TachError(f"{path} is not a Python file.")
        if os.path.exists(path.removesuffix(".py")):
            raise TachError("{path} already has a directory of the same name.")
        dirname = os.path.dirname(path)
        package_config = parse_package_config(dirname)
        if package_config is not None and package_config.strict:
            # this is a file we are pivoting out from a strict package
            # so any of its imports from siblings will start to fail, actually all relative imports will fail
            print(
                f"{BCOLORS.WARNING}'{path}' is contained by a strict package. "
                f"You may need to update imports from '{dirname}' to come through __all__ in __init__.py{BCOLORS.ENDC}"
            )
    root = fs.find_project_config_root(path)
    if not root:
        raise TachError(f"{CONFIG_FILE_NAME} does not exist in any parent directories")


def add_packages(paths: set[str], tags: Optional[set[str]]) -> Iterable[str]:
    new_tags: set[str] = set()
    # Validate paths
    for path in paths:
        validate_path(path=path)
    # Build packages
    for path in paths:
        new_tag = build_package(path=path, tags=tags)
        if new_tag:
            new_tags.add(new_tag)
    # Update project config
    project_root = fs.find_project_config_root(path=".")
    if not project_root:
        raise TachError(f"{CONFIG_FILE_NAME} not found.")
    warning = update_project_config(root=project_root, tags=tags if tags else new_tags)
    if warning:
        return [warning]
    return []
