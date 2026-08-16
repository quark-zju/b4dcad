.PHONY: dev-install

dev-install:
	# editable_mode=strict works better with zed/basedpyright
	python -m pip install -e . --config-settings editable_mode=strict
