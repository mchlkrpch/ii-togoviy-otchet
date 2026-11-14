"""CPU sentence embeddings with local weights and a bounded metadata-only cache."""

from collections import OrderedDict
import os
from pathlib import Path
from threading import Lock

import numpy as np


MODEL_PATH = Path(os.environ.get("AUDIT_MODEL_DIR", Path(__file__).resolve().parents[1] / ".models/rubert-tiny2"))


class LocalEncoder:
    def __init__(self, path=MODEL_PATH):
        self.path = Path(path)
        self.lock = Lock()
        self.model = None
        self.tokenizer = None
        self.vectors = OrderedDict()

    def load(self):
        if self.model is None:
            if not (self.path / "model.safetensors").is_file():
                raise ValueError("Веса rubert-tiny2 отсутствуют. Выполните python prepare_model.py в src.")
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            import torch
            from transformers import AutoModel, AutoTokenizer
            torch.set_num_threads(int(os.environ.get("AUDIT_MODEL_THREADS", "2")))
            self.tokenizer = AutoTokenizer.from_pretrained(str(self.path), local_files_only=True)
            self.model = AutoModel.from_pretrained(str(self.path), local_files_only=True,
                                                    use_safetensors=True).eval().to("cpu")

    def encode_batch(self, texts):
        import torch
        self.load()
        tokens = self.tokenizer(texts, padding=True, truncation=True, max_length=192, return_tensors="pt")
        with torch.inference_mode():
            output = self.model(**tokens).last_hidden_state[:, 0, :]
            return torch.nn.functional.normalize(output, dim=1).cpu().numpy()

    def similarities(self, query, texts):
        with self.lock:
            # Deduplication avoids repeated inference for aliases of one cell.
            unique = list(dict.fromkeys([query, *texts]))
            missing = [text for text in unique if text not in self.vectors]
            for start in range(0, len(missing), 32):
                batch = missing[start:start + 32]
                self.vectors.update(zip(batch, self.encode_batch(batch)))
            result = [float(np.dot(self.vectors[query], self.vectors[text])) for text in texts]
            while len(self.vectors) > 8192:
                self.vectors.popitem(last=False)
            return result


encoder = LocalEncoder()
