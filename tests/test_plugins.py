import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import webhook_manager
from event_bus import EventBus
from plugin_base import Plugin, PluginRegistry


class PluginRegistryTests(unittest.TestCase):
    def test_manifest_plugin_receives_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugins"
            demo_dir = plugin_dir / "demo"
            demo_dir.mkdir(parents=True)
            (demo_dir / "plugin.json").write_text(
                '{"enabled": true, "entry": "plugin.py", "config": {"answer": 42}}',
                encoding="utf-8",
            )
            (demo_dir / "plugin.py").write_text(
                "\n".join(
                    [
                        "from plugin_base import Plugin",
                        "class DemoPlugin(Plugin):",
                        "    name = 'demo'",
                        "    commands = ['/demo']",
                        "    def configure(self, config):",
                        "        super().configure(config)",
                        "PLUGIN_CLASS = DemoPlugin",
                    ]
                ),
                encoding="utf-8",
            )

            old_examples_dir = webhook_manager._EXAMPLES_DIR
            try:
                webhook_manager._EXAMPLES_DIR = plugin_dir
                registry = PluginRegistry(EventBus())
                webhook_manager.discover_and_register_plugins(registry)
            finally:
                webhook_manager._EXAMPLES_DIR = old_examples_dir

            self.assertEqual(len(registry.plugins), 1)
            self.assertEqual(registry.plugins[0].config, {"answer": 42})
            self.assertIs(registry.route_command("/demo", "uid-1"), registry.plugins[0])

    def test_dispatch_command_catches_plugin_exception(self):
        class BrokenPlugin(Plugin):
            name = "broken"
            commands = ["/broken"]

            def __init__(self):
                self.errors = []

            def handle(self, payload):
                raise RuntimeError("private detail")

            def on_error(self, exc, context=""):
                self.errors.append((str(exc), context))

        plugin = BrokenPlugin()
        registry = PluginRegistry(EventBus())
        registry.register(plugin)

        self.assertFalse(registry.dispatch_command(plugin, {"from_user": "uid-1"}))
        self.assertEqual(plugin.errors, [("private detail", "handle")])


if __name__ == "__main__":
    unittest.main()
