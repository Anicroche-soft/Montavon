.PHONY: build convert clean

VENV    = .venv
PYTHON  = $(VENV)/bin/python3
FONTMAKE = $(VENV)/bin/fontmake

# Convertir les SVG en .glif dans l'UFO
convert: $(VENV)
	$(PYTHON) scripts/svg_to_glif.py

# Compiler l'UFO en .ttf
build: $(VENV)
	mkdir -p dist
	$(FONTMAKE) -u sources/montavon.ufo -o ttf --output-dir dist

# Créer le virtualenv et installer les dépendances si besoin
$(VENV):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --quiet fontmake

# Supprimer les fichiers compilés
clean:
	rm -rf dist/
