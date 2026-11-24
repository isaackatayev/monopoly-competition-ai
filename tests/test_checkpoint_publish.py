import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import checkpoint_publish as publication


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(directory, revision):
    directory.mkdir(parents=True, exist_ok=True)
    value, behavior, offer = ((name+revision).encode() for name in ("board-", "opponent-", "offer-"))
    expert = json.dumps({"revision":revision,"weights":[.5]*20}).encode()
    base = {"format":"monopoly.neural.v1", "value_network":"neural.bin", "sha256":sha(value),
            "response_network":"response.bin", "response_sha256":sha(offer)}
    raw = (json.dumps(base,indent=2)+"\n").encode()
    active = {"format":"monopoly.adaptive.v1","base":"neural.json","base_sha256":sha(raw),
              "specialist":"specialist.json","specialist_sha256":sha(expert),
              "opponent":"opponent.bin","opponent_sha256":sha(behavior)}
    for name, data in (("neural.json",raw), ("neural.bin",value), ("response.bin",offer),
                       ("opponent.bin",behavior), ("specialist.json",expert),
                       ("adaptive.json",json.dumps(active).encode()), ("training-report.json",expert)):
        (directory/name).write_bytes(data)


def resolve(path, raw=None):
    # Read the actual public manifest and recursively verify all child checksums.
    document = json.loads(raw if raw is not None else path.read_bytes())
    if document["format"] == "monopoly.neural.v1":
        for name, sha_key in (("value_network","sha256"), ("response_network","response_sha256")):
            assert sha((path.parent/document[name]).read_bytes()) == document[sha_key]
        return (path.parent/document["value_network"]).read_bytes()
    for name in ("base", "opponent", "specialist"):
        assert sha((path.parent/document[name]).read_bytes()) == document[name+"_sha256"]
    return resolve(path.parent/document["base"])


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root/"deployed"
        self.logs = self.root/"logs"
        self.old = self.root/"old"
        self.new = self.root/"new"
        prepare(self.old,"one")
        prepare(self.new,"two")

    def publish(self, source, full):
        return publication.publish_checkpoint(source,self.target,self.logs,full,["training-report.json"])

    def test_publishes_actual_league_networks(self):
        import native
        import shutil
        source=self.root/"league"
        shutil.copytree(native.ROOT/"models",source)
        shutil.copyfile(source/"adaptive-v4.json",source/"adaptive.json")
        entry=publication.publish_checkpoint(source,self.target,self.logs,True,[])
        player=native.model(entry)
        self.assertEqual(player[0],11)
        self.assertEqual(player[8].sha256,json.loads(entry.read_bytes())["deal_value_sha256"])
        self.assertEqual(player[9].sha256,json.loads(entry.read_bytes())["pressure_sha256"])

    def test_migrates_legacy_before_updating_v1(self):
        prepare(self.target,"original")
        old_raw = (self.target/"neural.json").read_bytes()
        self.publish(self.old,False)
        self.assertEqual(resolve(self.target/"adaptive.json"),b"board-original")
        self.assertEqual(resolve(self.target/"neural.json"),b"board-one")
        old_base = json.loads((self.target/"adaptive.json").read_bytes())["base"]
        self.assertEqual((self.target/old_base).read_bytes(), old_raw)
        self.assertEqual((self.target/"neural.bin").read_bytes(),b"board-original")

        self.publish(self.new,True)
        self.assertEqual(resolve(self.target/"adaptive.json"),b"board-two")
        self.assertEqual(resolve(self.target/"neural.json"),b"board-two")
        self.assertEqual(resolve(self.target/old_base),b"board-original")

    def test_failing_then_retrying_keeps_valid_manifests(self):
        self.publish(self.old,True)
        old_manifest=(self.target/"adaptive.json").read_bytes()
        original=publication.replace_file
        def interrupt(path,data):
            if path == self.target/"adaptive.json":
                raise OSError("injected interruption")
            original(path,data)
        with patch.object(publication,"replace_file",interrupt):
            with self.assertRaises(OSError):
                self.publish(self.new,True)
        self.assertEqual(resolve(self.target/"adaptive.json"),b"board-one")
        self.assertEqual(resolve(self.target/"neural.json"),b"board-two")
        self.publish(self.new,True)
        self.assertEqual(resolve(self.target/"adaptive.json"),b"board-two")
        self.assertEqual(resolve(self.target/"adaptive.json",old_manifest),b"board-one")

    def test_concurrent_writers_and_bad_inputs(self):
        self.publish(self.old,True)
        def writer(i):
            self.publish(self.old if i%2 else self.new, full=i%3==0)
            # A reader may hold either old/new entry while other writers publish.
            self.assertIn(resolve(self.target/"adaptive.json"),(b"board-one",b"board-two"))
            self.assertIn(resolve(self.target/"neural.json"),(b"board-one",b"board-two"))
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(writer,range(12)))
        last=(self.target/"adaptive.json").read_bytes()
        (self.new/"neural.bin").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.publish(self.new,True)
        self.assertEqual(last,(self.target/"adaptive.json").read_bytes())
        resolve(self.target/"adaptive.json")

    def test_reader_retries_changed_legacy_entry_once(self):
        import native
        prepare(self.target,"original")
        entry=self.target/"adaptive.json"
        base=self.target/"neural.json"
        old_fields=json.loads(base.read_bytes())
        old_fields["prior_weights"]={name:.25 for name in native.NAMES}
        base.write_text(json.dumps(old_fields))
        old_entry=json.loads(entry.read_bytes())
        old_entry["base_sha256"]=sha(base.read_bytes())
        entry.write_text(json.dumps(old_entry))
        stale_entry=entry.read_bytes()
        self.publish(self.new,False)
        current=entry.read_bytes()

        # The reader observed the old entry before the publish. All subsequent
        # dependency reads see the newly published filesystem.
        def load(entries):
            reads=[]
            real_read=Path.read_bytes
            def timed_read(path):
                if path == entry:
                    result=entries[len(reads)]
                    reads.append(result)
                    return result
                return real_read(path)
            def test_network(path,checksum,kind="network"):
                self.assertEqual(sha(path.read_bytes()),checksum)
                return path.read_bytes()
            with patch.object(Path,"read_bytes",timed_read), patch.object(native,"Network",test_network):
                return native.model(entry)
        loaded=load([stale_entry,current])
        self.assertEqual(loaded[0],7)
        self.assertEqual(loaded[2],b"board-original")
        with self.assertRaisesRegex(ValueError,"base checksum"):
            load([stale_entry,stale_entry])
        corrupt=json.loads(current)
        corrupt["base_sha256"]="0"*64
        with self.assertRaisesRegex(ValueError,"base checksum"):
            load([stale_entry,json.dumps(corrupt).encode()])
