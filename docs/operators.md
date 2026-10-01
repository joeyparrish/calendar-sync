# Deploying Calendar Sync

Calendar Sync runs in your organization's Google Cloud: a public web UI and a
private worker on Cloud Run, Cloud Scheduler, Firestore, Cloud KMS, and Secret
Manager. A small organization fits mostly within the free tier.

## Before you start

You need:

- A Google Cloud project inside your Workspace organization, with billing.
- `gcloud`, signed in with your Workspace account.
- OpenTofu (`tofu`).
- Docker.
- These shell variables:

```sh
PROJECT=my-project-id
REGION=us-west1
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
```

The web UI's address is `https://calendar-sync-web-$PROJECT_NUMBER.$REGION.run.app`
unless you add a custom domain.

## 1. Bootstrap

```sh
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  --project $PROJECT
gcloud storage buckets create \
  gs://$PROJECT-tofu-state \
  --project $PROJECT \
  --location $REGION \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update gs://$PROJECT-tofu-state --versioning
gcloud auth application-default login
gcloud auth application-default set-quota-project $PROJECT
```

`application-default login` replaces any application-default credentials
already on this machine.

## 2. OAuth client (console)

In **Google Auth Platform** for your project:

1. **Get started**. App name `Calendar Sync`, a support email (a group works).
   **Next**.
2. Audience **Internal**. **Next**.
3. Contact email. **Next**, agree to the policy, **Continue**, **Create**.
4. **Clients**, **Create client**: type **Web application**, name
   `Calendar Sync Web`.
5. Authorized redirect URI: the web UI's address plus `/oauth/callback`.
   **Create**.
6. **Download JSON** and save it as `~/.config/calendar-sync/web-client.json`
   (mode 600).

Internal apps need no verification and show no permission checkboxes.

## 3. Settings

```sh
cp infra/example.tfvars infra/mine.tfvars
```

Edit `infra/mine.tfvars`. Git ignores every `.tfvars` file except the example.

## 4. First deploy

```sh
tofu -chdir=infra init -backend-config=bucket=$PROJECT-tofu-state
tofu -chdir=infra apply -var-file=mine.tfvars \
  -target=google_project_service.apis \
  -target=google_secret_manager_secret.oauth_client \
  -target=google_secret_manager_secret.session_key \
  -target=google_artifact_registry_repository.images
gcloud secrets versions add oauth-client --project $PROJECT \
  --data-file=$HOME/.config/calendar-sync/web-client.json
openssl rand -hex 32 | gcloud secrets versions add session-key \
  --project $PROJECT --data-file=-
gcloud auth configure-docker $REGION-docker.pkg.dev
docker build -t $REGION-docker.pkg.dev/$PROJECT/calendar-sync/app:v1 .
docker push $REGION-docker.pkg.dev/$PROJECT/calendar-sync/app:v1
tofu -chdir=infra apply -var-file=mine.tfvars
```

The first `apply` is targeted because the services need the secrets and the
image before they can start. Then open the web UI and sign in.

## Updating

Build and push a new tag, set `image` in your `.tfvars`, and run
`tofu -chdir=infra apply -var-file=mine.tfvars`.

## Custom domain (optional)

Cloud Run domain mapping is a pre-GA feature.

1. `gcloud domains list-user-verified` must list your domain. Workspace domain
   verification usually counts; if not, run `gcloud domains verify DOMAIN`.
2. Add `https://HOST/oauth/callback` to the OAuth client's redirect URIs.
3. Add a DNS record: `HOST` CNAME `ghs.googlehosted.com`. On Cloudflare, set it
   to **DNS only**.
4. Set `custom_domain = "HOST"` and create only the mapping:
   `tofu -chdir=infra apply -var-file=mine.tfvars -target=google_cloud_run_domain_mapping.web`
5. Wait until this shows `Ready` as `True`, then a few more minutes for HTTPS:
   `gcloud beta run domain-mappings describe --domain HOST --region $REGION --project $PROJECT`
6. `tofu -chdir=infra apply -var-file=mine.tfvars`. The run.app address now
   redirects to `HOST`.

## Running it

Sync everyone now:

```sh
gcloud scheduler jobs run calendar-sync --location $REGION --project $PROJECT
```

Read the worker's logs:

```sh
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="calendar-sync-worker"' \
  --project $PROJECT --limit 20
```

Alerts go to `admin_email`:

- **error logged**: something unexpected failed. Check the logs.
- **scheduler could not reach the worker**: the worker is down or misconfigured.
- **a user needs to sign in again**: ask the named user to sign in on the web UI.

Data lives in Firestore. Sign-in tokens and ICS links are encrypted with the
Cloud KMS key `calendar-sync/credentials`.
