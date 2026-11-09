"""Criterion 4: the ignore rules keep the design's working set out of the repository, and must
never take the Terraform with it."""

import subprocess
from pathlib import Path


def test_terraform_is_not_ignored():
    result = subprocess.run(
        ["git", "check-ignore", "build/infra/main.tf", "build/infra/adp.auto.tfvars.json"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""
    assert result.returncode == 1, result.stderr  # 1: none of the paths is ignored


def test_only_the_variables_template_ships():
    """A filled-in copy of the template holds the reader's own values, so it stays out of the
    repository under any name, while the template itself ships."""
    repo = Path(__file__).parent.parent
    filled = [
        "build/infra/terraform.tfvars",
        "build/infra/work.tfvars",
        "build/infra/secrets.auto.tfvars",  # Terraform loads *.auto.tfvars on its own
    ]
    ignored = subprocess.run(["git", "check-ignore", *filled], cwd=repo, capture_output=True, text=True)
    assert ignored.stdout.split() == filled, ignored.stderr

    template = subprocess.run(
        ["git", "check-ignore", "build/infra/terraform.tfvars.example"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert template.returncode == 1, template.stderr  # 1: not ignored
