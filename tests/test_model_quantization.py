import sys
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from tiny_brain.config import Settings
from scripts.benchmark_model import simulate_model_quantization_benchmark


def test_compose_yaml_model_quantization_and_kv_cache():
    compose_path = Path(__file__).parent.parent / "compose.yaml"
    content = compose_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    cmd = parsed["services"]["llama"]["command"]

    # 1. Chat model defaults to Q4_0
    model_idx = cmd.index("-m")
    assert "q4_0" in cmd[model_idx + 1].lower()

    # 2. KV cache Value tensor is f16 to avoid AVX2 dequantization penalty
    assert "-ctk" in cmd
    ctk_idx = cmd.index("-ctk")
    assert cmd[ctk_idx + 1] == "q8_0"

    assert "-ctv" in cmd
    ctv_idx = cmd.index("-ctv")
    assert cmd[ctv_idx + 1] == "f16"

    # 3. Runtime tensor repacking (--repack) is enabled
    assert "--repack" in cmd


def test_settings_chat_model_default_q4_0(tmp_path: Path):
    settings = Settings(
        tmp_path / "vault", tmp_path / "data", tmp_path / "db.sqlite", tmp_path / "models",
        "http://llama:8080/v1", "gemma-4-E2B-it-q4_0.gguf", "BAAI/bge-small-en-v1.5",
        "fastembed", 8192, 2, "tiny-brain.home.arpa", True, 0.9, True, "0.0.0.0", 8000,
    )
    assert "q4_0" in settings.chat_model


def test_model_quantization_benchmark_simulation():
    res = simulate_model_quantization_benchmark()
    assert "quantization_formats" in res
    assert "kv_cache_formats" in res
    assert "architecture_evaluations" in res

    q4_0 = next(q for q in res["quantization_formats"] if "Q4_0" in q["quant"])
    assert q4_0["speedup_vs_baseline"] > 1.0

    v_f16 = next(kv for kv in res["kv_cache_formats"] if "f16" in kv["setting"])
    assert v_f16["generation_speedup"] > 1.0

    qwen = next(m for m in res["architecture_evaluations"] if "Qwen" in m["model"])
    assert qwen["speedup_vs_gemma"] > 1.5
