name: Testy agenta

on:
  push:
    branches:
      - main
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - name: Pobierz repozytorium
        uses: actions/checkout@v4

      - name: Przygotuj Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Uruchom testy
        run: python -m unittest -v test_agent
