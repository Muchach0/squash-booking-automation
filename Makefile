
FRONTEND_IMAGE ?= squash-booking-frontend
FRONTEND_PORT ?= 5000
DOCKER_IMAGE_FRONTEND = muchachoo/squash-auto-frontend
DOCKER_IMAGE_FRONTEND_VERSION = v13
GCP_CLOUD_RUN_SERVICE_FRONTEND = squash-auto-frontend
DNS_ZONE = muchacho-app
DNS_NAME_CLIENT = squash-auto-resa.muchacho.app

GCP_PROJECT_ID ?= $(shell gcloud config get-value project 2>/dev/null)
GCP_PROJECT_NUMBER ?= 1036179882263
GCP_REGION ?= europe-west1
GCP_SCHEDULER_REGION ?= europe-west1



# Automation ARGS
DOCKER_IMAGE_AUTOMATION = muchachoo/squash-auto-booking
DOCKER_IMAGE_VERSION = v2
AUTOMATION_ARGS ?=
AUTOMATION_TEST_ARGS ?= --headless --debug
GCP_CLOUD_RUN_JOB_AUTOMATION ?= squash-auto-automation
GCP_CLOUD_RUN_JOB_SERVICE_ACCOUNT ?= $(GCP_PROJECT_NUMBER)-compute@developer.gserviceaccount.com
GCP_AUTOMATION_SCHEDULER_JOB ?= squash-auto-automation-daily
GCP_AUTOMATION_SCHEDULE ?= 1 0 * * *
GCP_AUTOMATION_TIME_ZONE ?= Europe/Paris
GCP_AUTOMATION_SECRET ?= squash_secrets


.PHONY: build-frontend run-frontend build-automation run-automation run-automation-test push-new-docker-image-frontend gcp-frontend-deploy gcp-init-dns push-new-docker-image-automation gcp-automation-enable-apis gcp-automation-deploy gcp-automation-run gcp-automation-create-scheduler gcp-automation-update-scheduler gcp-automation-grant-secret-access gcp-automation-grant-scheduler-invoker gcp-automation-deploy-all


#### 'FRONTEND' Web APP PART ####
build-frontend:
	docker build -f frontend/Dockerfile -t $(FRONTEND_IMAGE) frontend

run-frontend: build-frontend
	docker run --rm -p $(FRONTEND_PORT):5000 $(FRONTEND_IMAGE)

push-new-docker-image-frontend:
	cd frontend && docker build -t $(DOCKER_IMAGE_FRONTEND):$(DOCKER_IMAGE_FRONTEND_VERSION) -f Dockerfile .
	docker push $(DOCKER_IMAGE_FRONTEND):$(DOCKER_IMAGE_FRONTEND_VERSION)

gcp-frontend-deploy: push-new-docker-image-frontend
	@echo "Using Docker image:  $(DOCKER_IMAGE_FRONTEND):$(DOCKER_IMAGE_FRONTEND_VERSION)"
	gcloud run deploy $(GCP_CLOUD_RUN_SERVICE_FRONTEND) \
          --image $(DOCKER_IMAGE_FRONTEND):$(DOCKER_IMAGE_FRONTEND_VERSION) \
          --region $(GCP_REGION) \
          --platform managed \
          --allow-unauthenticated \
          --max-instances=1 \
          --port 5000 
	xdg-open https://$(DNS_NAME_CLIENT)

gcp-init-dns: ## Initialize DNS for GCP
	gcloud dns record-sets transaction start --zone=$(DNS_ZONE)
	gcloud dns record-sets transaction add --zone=$(DNS_ZONE) --name="$(DNS_NAME_CLIENT)" --type=CNAME --ttl=432000 "ghs.googlehosted.com."
	gcloud dns record-sets transaction execute --zone=$(DNS_ZONE)
	gcloud beta run domain-mappings create --service $(GCP_CLOUD_RUN_SERVICE_FRONTEND) --domain $(DNS_NAME_CLIENT) --region $(GCP_REGION)




##### AUTOMATION PART #####
build-automation:
	docker build -f automation/Dockerfile-auto-booking -t $(DOCKER_IMAGE_AUTOMATION):$(DOCKER_IMAGE_VERSION) automation

run-automation: build-automation
	docker run --rm $(DOCKER_IMAGE_AUTOMATION):$(DOCKER_IMAGE_VERSION) python /app/reserver_squash.py $(AUTOMATION_ARGS)

run-automation-test:
	docker run --volume ./automation:/app --rm $(DOCKER_IMAGE_AUTOMATION):$(DOCKER_IMAGE_VERSION) python /app/test.py $(AUTOMATION_TEST_ARGS)

push-new-docker-image-automation:
	docker build -f automation/Dockerfile-auto-booking -t $(DOCKER_IMAGE_AUTOMATION):$(DOCKER_IMAGE_VERSION) automation
	docker push $(DOCKER_IMAGE_AUTOMATION):$(DOCKER_IMAGE_VERSION)

gcp-automation-enable-apis:
	gcloud services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com

gcp-automation-deploy: push-new-docker-image-automation
	@echo "Deploying Cloud Run job: $(GCP_CLOUD_RUN_JOB_AUTOMATION)"
	gcloud run jobs deploy $(GCP_CLOUD_RUN_JOB_AUTOMATION) \
          --image $(DOCKER_IMAGE_AUTOMATION):$(DOCKER_IMAGE_VERSION) \
          --region $(GCP_REGION) \
          --tasks 1 \
          --parallelism 1 \
          --max-retries 0 \
          --task-timeout 30m \
          --service-account $(GCP_CLOUD_RUN_JOB_SERVICE_ACCOUNT)

gcp-automation-run:
	gcloud run jobs execute $(GCP_CLOUD_RUN_JOB_AUTOMATION) \
          --region $(GCP_REGION) \
          --wait

gcp-automation-create-scheduler:
	gcloud scheduler jobs create http $(GCP_AUTOMATION_SCHEDULER_JOB) \
          --location $(GCP_SCHEDULER_REGION) \
          --schedule="$(GCP_AUTOMATION_SCHEDULE)" \
          --time-zone="$(GCP_AUTOMATION_TIME_ZONE)" \
          --uri="https://run.googleapis.com/v2/projects/$(GCP_PROJECT_ID)/locations/$(GCP_REGION)/jobs/$(GCP_CLOUD_RUN_JOB_AUTOMATION):run" \
          --http-method POST \
          --oauth-service-account-email $(GCP_CLOUD_RUN_JOB_SERVICE_ACCOUNT)

gcp-automation-update-scheduler:
	gcloud scheduler jobs update http $(GCP_AUTOMATION_SCHEDULER_JOB) \
          --location $(GCP_SCHEDULER_REGION) \
          --schedule="$(GCP_AUTOMATION_SCHEDULE)" \
          --time-zone="$(GCP_AUTOMATION_TIME_ZONE)" \
          --uri="https://run.googleapis.com/v2/projects/$(GCP_PROJECT_ID)/locations/$(GCP_REGION)/jobs/$(GCP_CLOUD_RUN_JOB_AUTOMATION):run" \
          --http-method POST \
          --oauth-service-account-email $(GCP_CLOUD_RUN_JOB_SERVICE_ACCOUNT)



gcp-automation-grant-scheduler-invoker:
	gcloud run jobs add-iam-policy-binding $(GCP_CLOUD_RUN_JOB_AUTOMATION) \
          --region $(GCP_REGION) \
          --member="serviceAccount:$(GCP_CLOUD_RUN_JOB_SERVICE_ACCOUNT)" \
          --role="roles/run.invoker"

gcp-automation-deploy-all: gcp-automation-enable-apis gcp-automation-deploy gcp-automation-grant-scheduler-invoker gcp-automation-create-scheduler
