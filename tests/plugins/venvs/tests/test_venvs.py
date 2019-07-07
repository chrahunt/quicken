def test_venvs_creates_venv_with_pip(venvs):
    venv = venvs.create()
    result = venv.run(["-m", "pip", "-V"])
    assert result.returncode == 0
