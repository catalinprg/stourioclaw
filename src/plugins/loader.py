"""Plugin loaders for YAML-defined and Python-class-based tools."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os

import yaml

from src.plugins.base import BaseTool
from src.plugins.yaml_tool import YamlTool

logger = logging.getLogger("stourio.plugins.loader")


def load_yaml_tools(directory: str) -> list[BaseTool]:
    """Load all .yaml / .yml files in *directory* and return YamlTool instances."""
    tools: list[BaseTool] = []

    if not os.path.isdir(directory):
        logger.debug("YAML tools directory not found, skipping: %s", directory)
        return tools

    for filename in sorted(os.listdir(directory)):
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue

        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                definition = yaml.safe_load(fh)

            if not isinstance(definition, dict):
                logger.warning("YAML tool file '%s' did not parse to a dict, skipping.", filepath)
                continue

            if "name" not in definition:
                logger.warning("YAML tool file '%s' missing 'name' field, skipping.", filepath)
                continue

            tool = YamlTool(definition)
            tools.append(tool)
            logger.info("Loaded YAML tool: %s (from %s)", tool.name, filename)

        except yaml.YAMLError as exc:
            logger.error("YAML parse error in '%s': %s", filepath, exc)
        except Exception as exc:
            logger.error("Failed to load YAML tool from '%s': %s", filepath, exc)

    return tools


def load_python_tools(directory: str) -> list[BaseTool]:
    """Auto-discover BaseTool subclasses in all .py files under *directory*."""
    tools: list[BaseTool] = []

    if not os.path.isdir(directory):
        logger.debug("Python tools directory not found, skipping: %s", directory)
        return tools

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        filepath = os.path.join(directory, filename)
        module_name = f"stourio_tools.{filename[:-3]}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                logger.warning("Cannot create module spec for '%s', skipping.", filepath)
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseTool)
                    and obj is not BaseTool
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance: BaseTool = obj()
                        tools.append(instance)
                        logger.info(
                            "Loaded Python tool: %s (from %s)", instance.name, filename
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to instantiate tool class '%s' from '%s': %s",
                            attr_name, filepath, exc,
                        )

        except Exception as exc:
            logger.error("Failed to import Python tool module '%s': %s", filepath, exc)

    return tools
