from unittest.mock import Mock

from app.schemas.vision_result import VisionResult
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.failover_provider import FailoverVisionProvider


def result(answer: str) -> VisionResult:
    return VisionResult(image_type="other", answer=answer, items=[], uncertain_items=[])


def test_nvidia_success_does_not_call_ollama():
    nvidia = Mock()
    nvidia.infer.return_value = result("nvidia")
    ollama = Mock()

    actual = FailoverVisionProvider(nvidia, ollama).infer("system", "user", "image")

    assert actual.answer == "nvidia"
    nvidia.infer.assert_called_once_with("system", "user", "image")
    ollama.infer.assert_not_called()


def test_nvidia_failure_calls_ollama_once_with_same_request():
    nvidia = Mock()
    nvidia.infer.side_effect = VisionModelUnavailableError("timeout")
    ollama = Mock()
    ollama.infer.return_value = result("ollama")

    actual = FailoverVisionProvider(nvidia, ollama).infer("system", "user", "image")

    assert actual.answer == "ollama"
    nvidia.infer.assert_called_once_with("system", "user", "image")
    ollama.infer.assert_called_once_with("system", "user", "image")


def test_malformed_nvidia_200_response_calls_ollama_once():
    nvidia = Mock()
    nvidia.infer.side_effect = ValueError("invalid VisionResult")
    ollama = Mock()
    ollama.infer.return_value = result("ollama")

    FailoverVisionProvider(nvidia, ollama).infer("system", "user", "image")

    assert nvidia.infer.call_count == 1
    assert ollama.infer.call_count == 1
