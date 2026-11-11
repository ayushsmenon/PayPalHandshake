import importlib
from pathlib import Path
from unittest.mock import patch

import app.main as main


def test_application_loads_service_environment_without_overriding_external_values() -> None:
    expected_path = Path(main.__file__).resolve().parents[1] / ".env"

    with patch("dotenv.load_dotenv") as load_dotenv:
        importlib.reload(main)

    load_dotenv.assert_called_once_with(expected_path, override=False)
