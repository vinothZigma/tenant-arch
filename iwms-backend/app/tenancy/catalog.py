"""Which tables live where.

Master (the default DB) holds things that belong to the platform, not to any
one company: the company list itself, platform users, and global lookups that
every company reads. Everything else in the `app` app is company data and
lives in that company's own DB.

Adding a model? It is company data unless you add it here. Global lookups
must not carry a company_id; company data must not be referenced by a real
ForeignKey from a master model (Django can't join across databases).
"""

# Lower-case model names (Model._meta.model_name) of the `app` app that stay
# in the master DB.
MASTER_MODELS = frozenset({
    "company",
    # Platform super admins and (until company logins move into the company
    # DBs) every login. Account.user is a no-constraint FK because of this.
    "user",
    # One scheduler process for the whole server (a server setting).
    "schedulerconfig",
    # Plans, modules and billing (app/models/tenancy/master.py).
    "plan",
    "module",
    "companymodule",
    "planmodulelimit",
    "extrarecordpurchase",
    "planchangerequest",
    "formconfig",
})

TENANT_APP_LABEL = "app"


def is_master_model(app_label, model_name):
    """True when (app_label, model_name) belongs in the master DB.

    Django's own apps (auth, admin, sessions, contenttypes) are master-only.
    """
    if app_label != TENANT_APP_LABEL:
        return True
    return (model_name or "").lower() in MASTER_MODELS


def is_tenant_model(model):
    meta = model._meta
    return not is_master_model(meta.app_label, meta.model_name)
