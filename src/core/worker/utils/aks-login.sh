#!/bin/bash

set -euo pipefail

if [ $# -ne 4 ]; then
  echo "Usage: $0 <resource-group> <cluster-name> <namespace>"
  exit 1
fi

RESOURCE_GROUP=$2
CLUSTER_NAME=$3
NAMESPACE=$4
TOKEN_TTL=600  # token valid for 10 minutes

export KUBECONFIG=$1

log() { echo "[aks-login] $*"; }

# Azure CLI login (Managed Identity) and subscription selection are handled
# once per execution by az-login.sh before this script runs; this script only
# sets up AKS/kubectl access on top of that already-authenticated session.

# Fetch kubeconfig
log "Fetching AKS credentials for ${CLUSTER_NAME}..."
az aks get-credentials --file "$KUBECONFIG" --resource-group "$RESOURCE_GROUP" --name "$CLUSTER_NAME" --overwrite-existing

# Convert kubeconfig to MSI with token TTL 10 minutes
log "Converting kubeconfig to MSI via kubelogin (force, token ${TOKEN_TTL}s)..."
if [[ -n "${AZURE_CLIENT_ID:-}" ]]; then
  kubelogin --kubeconfig "$KUBECONFIG" convert-kubeconfig -l msi --client-id "$AZURE_CLIENT_ID"
else
  kubelogin --kubeconfig "$KUBECONFIG" convert-kubeconfig -l msi
fi

# Limit context to the target namespace
kubectl config set-context --kubeconfig "$KUBECONFIG" --current --namespace="$NAMESPACE"

# Probe non-interactive access
set +e
kubectl get --raw=/readyz 1>/dev/null 2>&1
READY_RC=$?
set -e

if [[ $READY_RC -ne 0 ]]; then
  log "Probe failed; applying exec-plugin fallback with token ${TOKEN_TTL}s..."

  USER_NAME="msi-${CLUSTER_NAME}"

  if [[ -n "${AZURE_CLIENT_ID:-}" ]]; then
    kubectl config --kubeconfig "$KUBECONFIG" set-credentials "$USER_NAME" \
      --exec-command=kubelogin \
      --exec-api-version=client.authentication.k8s.io/v1beta1 \
      --exec-arg=get-token \
      --exec-arg=--login \
      --exec-arg=msi \
      --exec-arg=--client-id \
      --exec-arg="$AZURE_CLIENT_ID"
  else
    kubectl config --kubeconfig "$KUBECONFIG" set-credentials "$USER_NAME" \
      --exec-command=kubelogin \
      --exec-api-version=client.authentication.k8s.io/v1beta1 \
      --exec-arg=get-token \
      --exec-arg=--login \
      --exec-arg=msi
  fi

  CURRENT_CONTEXT="$(kubectl config current-context)"
  kubectl config --kubeconfig "$KUBECONFIG" set-context "$CURRENT_CONTEXT" --user="$USER_NAME" --namespace="$NAMESPACE"

  set +e
  kubectl get --raw=/readyz 1>/dev/null 2>&1
  READY_RC=$?
  set -e

  if [[ $READY_RC -ne 0 ]]; then
    log "Still failing to authenticate non-interactively. Check RBAC/permissions."
    exit 1
  fi
fi

# Switch context user to a dedicated ServiceAccount in the target namespace (TokenRequest TTL 10m)
log "Preparing RBAC and token in namespace '$NAMESPACE'"

# Ensure ServiceAccount exists (best-effort, log outcome)
if ! kubectl -n "$NAMESPACE" get sa cloudo-sa-$NAMESPACE 1>/dev/null 2>&1; then
  if kubectl -n "$NAMESPACE" create sa cloudo-sa-$NAMESPACE; then
    log "ServiceAccount created: cloudo-sa-$NAMESPACE"
  else
    log "WARN: cannot create ServiceAccount cloudo-sa-$NAMESPACE (forbidden?). Continuing if pre-provisioned."
  fi
fi

# Skip RBAC apply if we don't have rights on roles/rolebindings
if kubectl auth can-i get roles -n "$NAMESPACE" 1>/dev/null 2>&1 && \
   kubectl auth can-i create roles -n "$NAMESPACE" 1>/dev/null 2>&1 && \
   kubectl auth can-i create rolebindings -n "$NAMESPACE" 1>/dev/null 2>&1; then
  # Apply namespaced Role + RoleBinding
  kubectl -n "$NAMESPACE" apply -f - <<YAML
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: cloudo-maintainer-${NAMESPACE}
rules:
  - apiGroups: ["apps"]
    resources: ["deployments","deployments/scale","replicasets","statefulsets","statefulsets/scale","daemonsets"]
    verbs: ["get","list","watch","create","update","patch","delete"]
  - apiGroups: ["batch"]
    resources: ["jobs","cronjobs"]
    verbs: ["get","list","watch","create","update","patch","delete"]
  - apiGroups: ["autoscaling"]
    resources: ["horizontalpodautoscalers"]
    verbs: ["get","list","watch","create","update","patch","delete"]
  - apiGroups: ["keda.sh"]
    resources: ["scaledobjects"]
    verbs: ["get","list","watch","create","update","patch","delete"]
  - apiGroups: [""]
    resources: ["pods","pods/log","pods/exec","services","configmaps","secrets","endpoints","persistentvolumeclaims","replicationcontrollers","events"]
    verbs: ["get","list","watch","create","update","patch","delete"]
  - apiGroups: ["metrics.k8s.io"]
    resources: ["pods"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: cloudo-maintainer-binding-${NAMESPACE}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: cloudo-maintainer-${NAMESPACE}
subjects:
  - kind: ServiceAccount
    name: cloudo-sa-${NAMESPACE}
YAML
  log "RBAC applied (namespace '$NAMESPACE')"
else
  log "No rights to manage RBAC in '$NAMESPACE'. Expecting pre-provisioned SA/Role/RoleBinding."
fi


# Generate short-lived token (TokenRequest) with TTL 10 minutes
log "Trying to get token (TTL ${TOKEN_TTL}s) for SA cloudo-sa-$NAMESPACE"
if TOKEN=$(kubectl -n "$NAMESPACE" create token cloudo-sa-$NAMESPACE --duration="${TOKEN_TTL}s" 2> >(tee /dev/stderr)); then
  kubectl config --kubeconfig "$KUBECONFIG" set-credentials "sa-$NAMESPACE-cloudo" --token="$TOKEN"

  kubectl config --kubeconfig "$KUBECONFIG" set-context --current --user="sa-$NAMESPACE-cloudo" --namespace="$NAMESPACE"

  kubectl config --kubeconfig "$KUBECONFIG" delete-user "msi-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl config --kubeconfig "$KUBECONFIG" delete-user "clusterUser_${RESOURCE_GROUP}_${CLUSTER_NAME}" >/dev/null 2>&1 || true

else
  log "TokenRequest failed; verify RBAC and permissions."
  exit 1
fi

log "AKS login completed (non-interactive, token valid ${TOKEN_TTL}s) for namespace: $NAMESPACE"
