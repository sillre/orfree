"""End-to-end test of the import flow against a throwaway copy of CC Switch.

The real ``~/.cc-switch`` and ``~/.codex`` are copied into a temp directory
and the module paths are pointed at the copies, so the test never touches the
user's live installation.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL_HOME = os.path.join(os.path.expanduser("~"), ".cc-switch")
REAL_CODEX = os.path.join(os.path.expanduser("~"), ".codex")
ORIGINAL_PROVIDER = "896b19f4-21db-475e-9c56-c9dff9f56fab"

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    """Record a check result."""
    if condition:
        print("  PASS %s" % message)
    else:
        print("  FAIL %s" % message)
        failures.append(message)


class FakeModel:
    """A stand-in for an OpenRouter model."""

    def __init__(self, model_id: str) -> None:
        self.id = model_id
        self.name = model_id
        self.context_length = 262144
        self.input_modalities = ["text", "image"]
        self.supports_reasoning = True
        self.supports_tools = True
        self.free = model_id.endswith(":free")


def original_models() -> list[dict]:
    """The model list of the original provider card."""
    import sqlite3

    connection = sqlite3.connect(
        "file:%s?mode=ro" % os.path.join(REAL_HOME, "cc-switch.db"), uri=True
    )
    row = connection.execute(
        "SELECT settings_config FROM providers WHERE id = ?", (ORIGINAL_PROVIDER,)
    ).fetchone()
    if not row:
        return []
    return json.loads(row[0]).get("modelCatalog", {}).get("models", [])


def main() -> int:
    """Run the sandbox test."""
    if not os.path.exists(os.path.join(REAL_HOME, "cc-switch.db")):
        print("SKIP: no CC Switch installation to copy")
        return 0

    workspace = tempfile.mkdtemp(prefix="orfree-test-")
    try:
        home = os.path.join(workspace, "cc-switch")
        codex = os.path.join(workspace, "codex")
        os.makedirs(home)
        os.makedirs(codex)
        # Copy only the files this tool touches; the live Codex directory holds
        # locked sockets and lock files that cannot be copied.
        for name in ("cc-switch.db", "live-state.json"):
            source = os.path.join(REAL_HOME, name)
            if os.path.exists(source):
                shutil.copy2(source, os.path.join(home, name))
        for name in ("config.toml", "cc-switch-model-catalog.json"):
            source = os.path.join(REAL_CODEX, name)
            if os.path.exists(source):
                shutil.copy2(source, os.path.join(codex, name))

        # Give the sandbox its own routing state so the test does not depend
        # on whatever card happens to be routed in the real installation.
        with open(os.path.join(home, "live-state.json"), encoding="utf-8") as handle:
            state = json.load(handle)
        state["apps"]["codex"]["proxy_route"] = ORIGINAL_PROVIDER
        with open(os.path.join(home, "live-state.json"), "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False)

        if not os.path.exists(os.path.join(home, "live-state.json")):
            print("SKIP: no live-state.json to copy")
            return 0

        catalog_path = os.path.join(codex, "cc-switch-model-catalog.json")
        with open(catalog_path, encoding="utf-8") as handle:
            catalog_before = handle.read()

        os.environ["ORFREE_CC_SWITCH_HOME"] = home
        os.environ["ORFREE_CODEX_DIR"] = codex

        from orfree import catalog, ccswitch, ccswitch_writer

        print("1. create a provider")
        original = ccswitch.live_state()["apps"]["codex"]["proxy_route"]
        check(original == ORIGINAL_PROVIDER, "the sandbox routes a known provider")
        original_models = ccswitch.provider_row(original)["settings_config"]["modelCatalog"]["models"]

        result = ccswitch_writer.create_provider(
            name="orfree test card",
            models=[
                FakeModel("poolside/laguna-s-2.1:free"),
                FakeModel("cohere/north-mini-code:free"),
            ],
            api_key="sk-test-key",
        )
        provider_id = result.provider_id
        check(bool(provider_id), "a provider id was generated")

        row = ccswitch.provider_row(provider_id)
        check(row is not None, "the provider row is readable")
        specs = row["settings_config"]["modelCatalog"]["models"]
        stored = [spec["model"] for spec in specs]
        original_ids = [spec["model"] for spec in original_models]
        check(
            "poolside/laguna-s-2.1:free" in stored,
            "the newly picked model was stored",
        )
        check(
            "cohere/north-mini-code:free" in stored,
            "the second newly picked model was stored",
        )
        check(
            all(model in stored for model in original_ids),
            "the models that work today were kept",
        )
        check(
            "poolside/laguna-s-2.1:free" in stored,
            "the model id is preserved verbatim",
        )
        new_spec = next(
            spec for spec in specs if spec["model"] == "poolside/laguna-s-2.1:free"
        )
        check(
            new_spec["inputModalities"] == ["text", "image"],
            "input modalities are text plus image",
        )

        print("2. the previously routed card is untouched")
        after = ccswitch.provider_row(original)
        check(after is not None, "the old card still exists")
        check(
            after["settings_config"]["modelCatalog"]["models"] == original_models,
            "the old card's model list is unchanged",
        )

        print("3. routing")
        ccswitch_writer.route_to(provider_id)
        state = ccswitch.live_state()
        check(
            state["apps"]["codex"]["proxy_route"] == provider_id,
            "live-state points at the new card",
        )
        check(state["apps"]["codex"]["attached"] is True, "the app stays attached")
        check(state["apps"]["codex"]["mode"] == "proxy", "the mode stays proxy")

        print("4. the Codex catalog was rewritten")
        with open(catalog_path, encoding="utf-8") as handle:
            written = handle.read()
        check(
            written == catalog.dump_catalog(catalog.build_catalog(specs)),
            "the catalog matches what CC Switch would write",
        )
        with open(os.path.join(codex, "config.toml"), encoding="utf-8") as handle:
            config = handle.read()
        config_model = [
            line for line in config.splitlines() if line.startswith("model ")
        ][0].split("=")[1].strip().strip('"')
        check(
            config_model in stored,
            "config.toml names a model that is on the card",
        )

        print("5. backup and restore")
        check(
            os.path.isdir(os.path.join(home, "backups")),
            "a backup folder was created",
        )
        restored = ccswitch.restore_backup(ccswitch.latest_backup())
        check(len(restored) >= 2, "the backup restored several files")
        with open(catalog_path, encoding="utf-8") as handle:
            check(
                handle.read() == catalog_before,
                "the catalog is back to its old content",
            )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    print("")
    if failures:
        print("FAILED: %d check(s)" % len(failures))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
