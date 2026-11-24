"""Publish immutable, checksummed player bundles and atomically select the active manifest."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile


def encoded(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False)+"\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verified_asset(manifest_path, fields, key, checksum_key):
    data = (manifest_path.parent / fields[key]).read_bytes()
    if digest(data) != fields[checksum_key]:
        raise ValueError(f"{manifest_path.name} has an invalid {key} checksum")
    return data


def canonical_bytes(raw, fields, updates):
    if all(fields[key] == value for key, value in updates.items()):
        return raw
    return encoded({**fields, **updates})


def gather(directory, adaptive, logs=()):
    """Read dependencies through their manifests; bundle paths are local to the bundle."""
    files = {}
    base = directory/"neural.json"
    if adaptive:
        active = directory/"adaptive.json"
        raw = active.read_bytes()
        fields = json.loads(raw)
        if fields["format"] in ("monopoly.adaptive.v2","monopoly.adaptive.v3","monopoly.adaptive.v4"):
            files["resilience.json"] = verified_asset(active, fields, "resilience","resilience_sha256")
        if fields["format"] in ("monopoly.adaptive.v3","monopoly.adaptive.v4"):
            files["robust.json"] = verified_asset(active, fields, "robust","robust_sha256")
            if "deal_value" in fields:
                files["deal.bin"] = verified_asset(active, fields, "deal_value","deal_value_sha256")
        if fields["format"] == "monopoly.adaptive.v4":
            files["pressure.json"] = verified_asset(active, fields, "pressure","pressure_sha256")
        files["opponent.bin"] = verified_asset(active, fields, "opponent", "opponent_sha256")
        files["specialist.json"] = verified_asset(active, fields, "specialist", "specialist_sha256")
        base = active.parent/fields["base"]
        raw_base = verified_asset(active, fields, "base", "base_sha256")
    else:
        raw_base = base.read_bytes()
    base_fields = json.loads(raw_base)
    files["neural.bin"] = verified_asset(base, base_fields, "value_network", "sha256")
    files["response.bin"] = verified_asset(base, base_fields, "response_network", "response_sha256")
    files["neural.json"] = canonical_bytes(raw_base, base_fields, {"value_network":"neural.bin",
                                                                  "response_network":"response.bin"})
    if adaptive:
        files["adaptive.json"] = canonical_bytes(raw, fields, dict(base="neural.json",
                  base_sha256=digest(files["neural.json"]), opponent="opponent.bin", specialist="specialist.json",
                  **({"resilience":"resilience.json"} if "resilience.json" in files else {}),
                  **({"robust":"robust.json"} if "robust.json" in files else {}),
                  **({"deal_value":"deal.bin"} if "deal.bin" in files else {}),
                  **({"pressure":"pressure.json"} if "pressure.json" in files else {})))
    for name in logs:
        files[name] = (directory/name).read_bytes()
    return files


def install(store, files):
    # Entire directory becomes visible in one rename. A previous successful
    # installation is reusable after interruption anywhere in the publication.
    key = digest(encoded({n: digest(data) for n, data in sorted(files.items())}))
    bundle = store/key
    if bundle.exists():
        for name, data in files.items():
            if (bundle/name).read_bytes() != data:
                raise ValueError(f"Existing immutable bundle contains a changed {name}")
        return bundle
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=store) as tmp:
        staged = Path(tmp)/"bundle"
        staged.mkdir()
        for name, data in files.items():
            with (staged/name).open("wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
        staged.rename(bundle)
    return bundle


def replace_file(path, data):
    with tempfile.NamedTemporaryFile(prefix=".publish-", dir=path.parent, delete=False) as tmp:
        pending = Path(tmp.name)
        try:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def publish_entry(target, bundle, name):
    fields = json.loads((bundle/name).read_bytes())
    keys = ("base","opponent","specialist") if name == "adaptive.json" else ("value_network","response_network")
    if fields["format"] in ("monopoly.adaptive.v2","monopoly.adaptive.v3","monopoly.adaptive.v4"):
        keys = (*keys,"resilience")
    if fields["format"] in ("monopoly.adaptive.v3","monopoly.adaptive.v4"):
        keys = (*keys,"robust")
        if "deal_value" in fields:
            keys = (*keys,"deal_value")
    if fields["format"] == "monopoly.adaptive.v4":
        keys = (*keys,"pressure")
    for key in keys:
        fields[key] = str(bundle.relative_to(target)/fields[key])
    replace_file(target/name, encoded(fields))


def publish_checkpoint(stage, target, reports, adaptive, logs):
    stage, target, reports = map(Path, (stage, target, reports))
    files = gather(stage, adaptive, logs)
    store = target/"checkpoints"
    store.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    with (store/".publish.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        bundle = install(store, files)
        legacy_entry = target/"adaptive.json"
        if legacy_entry.exists():
            previous = json.loads(legacy_entry.read_bytes())
            if (target/previous["base"]).resolve() == (target/"neural.json").resolve():
                # A legacy adaptive entry pins the checksum of the MUTABLE v1
                # entry at the root. Freeze its original dependencies first.
                old_bundle = install(store, gather(target, adaptive=True))
                publish_entry(target, old_bundle, "adaptive.json")
        publish_entry(target, bundle, "neural.json")
        if adaptive:
            publish_entry(target, bundle, "adaptive.json")
        # A release's original reports are always retained beside its weights.
        # These top-level copies provide the traditional latest-report paths.
        for name in logs:
            replace_file(reports/name, files[name])
    return target/("adaptive.json" if adaptive else "neural.json")
