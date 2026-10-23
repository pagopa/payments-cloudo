"""Azure Functions entrypoint: the app is assembled from the route blueprints."""

import azure.functions as func
from routes import (
    approvals,
    audit_logs,
    auth,
    dev_testrun,
    logs,
    receiver,
    runbooks,
    schedules,
    schemas,
    settings,
    system,
    teams,
    trigger,
    users,
    workers,
)

app = func.FunctionApp()
app.register_functions(auth.bp)
app.register_functions(teams.bp)
app.register_functions(users.bp)
app.register_functions(settings.bp)
app.register_functions(audit_logs.bp)
app.register_functions(trigger.bp)
app.register_functions(approvals.bp)
app.register_functions(receiver.bp)
app.register_functions(dev_testrun.bp)
app.register_functions(system.bp)
app.register_functions(logs.bp)
app.register_functions(workers.bp)
app.register_functions(schedules.bp)
app.register_functions(schemas.bp)
app.register_functions(runbooks.bp)
