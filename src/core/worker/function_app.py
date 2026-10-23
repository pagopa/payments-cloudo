"""Azure Functions entrypoint: the app is assembled from the route blueprints."""

import azure.functions as func
from routes import processes, runbooks, system

app = func.FunctionApp()
app.register_functions(runbooks.bp)
app.register_functions(system.bp)
app.register_functions(processes.bp)
