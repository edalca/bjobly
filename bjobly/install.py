import click

from bjobly.setup import after_install as setup


def after_install():
	try:
		print("Setting up BJOBLY Settings to Frappe HR...")
		setup()

		click.secho("Thank you for installing BJOBLY!", fg="green")

	except Exception as e:
		click.secho(
			"Installation for BJOBLY app failed due to an error."
			" Please try re-installing the app or",
			fg="bright_red",
		)
		raise e