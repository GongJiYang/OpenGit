# OpenGit GitOps (K8s + ArgoCD)

This repository contains Kubernetes manifests for AgentHub/OpenGit using a **GitOps** workflow.

---

## Structure

```
opengit-infra/
  base/
    api-gateway/
    observer-ui/
    postgres/
    ingress.yaml
    namespace.yaml
  overlays/
    dev/
    staging/
    prod/
  argocd/
    application.yaml
```

---

## Apply (local kube)

```bash
kubectl apply -k overlays/dev
```

---

## GitOps Flow

1. App repo builds images (tag = git SHA)
2. CI updates `overlays/*/kustomization.yaml` image tags
3. ArgoCD auto-syncs to the cluster

---

## Secrets

Do **not** commit real secrets.  
Use one of:

- **SOPS + age** (`secret.sops.yaml`)
- **External Secrets** (Vault / AWS / GCP)

Required keys:
- `E2B_API_KEY`
- `ZHIPUAI_API_KEY`
- `QDRANT_URL`
- `DATABASE_URL`
- `AUTH_DATABASE_URL`
- `EXTERNAL_CI_SECRET` (optional)

---

## Notes

- `api-gateway` needs a **persistent volume** for `agenthub_data`
- Multi-replica **requires RWX storage**
- `observer-ui` uses build-time `NEXT_PUBLIC_API_BASE=/api`
