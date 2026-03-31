# CI/CD (GitHub Actions + Terraform) 가이드

## Phase 0: 사전 결정

- **배포 주체:** GitHub Actions 러너에서 `terraform` 실행.
- **스택 2개**
  - `aws-observability-platform/` (플랫폼)
  - `aws-observability-platform/aws_customer_app/` (고객 앱)
- 워크플로를 나누거나, 한 잡 안에서 **순서대로** 실행.
- **순서:** 플랫폼 `apply` 완료 후 NLB DNS 확보 → 고객 앱 `apply` 시 `otel_gateway_endpoint`에 `NLB-DNS:4317` 반영.

---

## Phase 1: AWS — Terraform State (CI에서 사실상 필수)

로컬 `terraform.tfstate`만 쓰면 Actions 러너마다 state가 비어 있어 **재적용·파괴 위험**이 큽니다.

**S3 버킷**과 **DynamoDB 잠금 테이블**은 **플랫폼 Terraform과 별도로**, 계정/리전에 **한 번 준비**해 두는 방식이 일반적입니다. (콘솔, CLI, 또는 아주 작은 “bootstrap” Terraform으로 생성.)

    {
| 리소스 | 예시 | 비고 |
|--------|------|------|
| S3 | `logtech-tfstate-apne2` | **버저닝** 켜기 |
| DynamoDB | 테이블명 `terraform-locks`, PK **`LockID`** | state 잠금용 |

State **키** 예시:

- `observability-platform/terraform.tfstate`
- `aws-customer-app/terraform.tfstate`

레포의 `terraform` 블록에 `backend "s3"` 를 넣거나, `terraform init -backend-config=...` 로 연결합니다.

```text
bucket, key, region, dynamodb_table, encrypt = true
```

---

## Phase 2: AWS — GitHub Actions용 IAM (OIDC 권장)

1. IAM → Identity providers → OpenID Connect → GitHub (`token.actions.githubusercontent.com`).
2. IAM **Role** 생성:
   - **Trust policy:** `sub`를 `repo:YOUR_ORG/YOUR_REPO:ref:refs/heads/main` 등으로 제한.
   - **Permission:** Terraform이 만드는 리소스에 맞는 정책 (처음엔 넓게, 나중에 최소화).
3. Role ARN을 메모 (예: `arn:aws:iam::123456789012:role/gh-terraform`).

**대안:** IAM 사용자 Access Key를 GitHub Secrets에 넣는 방식 — 가능하지만 OIDC가 권장.

---

## Phase 3: GitHub — Secrets / Variables

### 플랫폼 (`variables.tf` 기준, CI에서 `TF_VAR_*` 로 주입)

| 이름 (예시) | 용도 |
|-------------|------|
| `TF_VAR_opensearch_master_password` | OpenSearch 마스터 비밀번호 |
| `TF_VAR_slack_bot_token` | 기본값 없음 → **필수** |
| `TF_VAR_slack_channel` | 기본값 없음 → **필수** |
| `TF_VAR_slack_signing_secret` | 선택 (빈 문자열이면 기본) |
| `TF_VAR_agentcore_memory_id` 등 | 사용 시 |
| `TF_VAR_langsmith_api_key` | 사용 시 |

**EC2 키 페어 / `ec2_key_path`:** 현재 플랫폼 Terraform에는 **없음**. 게이트웨이 EC2는 **Session Manager(SSM)** 로 접속 (`AmazonSSMManagedInstanceCore`는 인스턴스 역할에 이미 연결됨). CI 파이프라인은 키가 필요 없고, **수동으로 SSM 접속**할 운영자 IAM에만 `ssm:StartSession` 등 권한이 있으면 됩니다.

### 고객 앱

| 이름 | 용도 |
|------|------|
| `TF_VAR_ec2_ami_id` | AMI |
| `TF_VAR_otel_gateway_endpoint` | `xxx.elb....amazonaws.com:4317` (플랫폼 배포 후 값) |

**`TF_VAR_ec2_key_name`:** 사용하지 않음. 고객 앱 EC2도 **SSM + 인스턴스 프로파일**로 접속.

`otel_gateway_endpoint`를 자동으로 넣으려면: 같은 워크플로에서 플랫폼 `apply` 후 `terraform output -raw otel_gateway_nlb_dns`로 읽어 `-var="otel_gateway_endpoint=${DNS}:4317"` 로 넘기면 됩니다.

---

## Phase 4: GitHub — Environment (선택, 권장)

Repo → Settings → Environments → `production` 생성.  
**Required reviewers** 설정 → `terraform apply` 전 승인.

---

## Phase 5: 워크플로 파일 추가

레포 루트에 예: `.github/workflows/terraform-platform.yml`

**공통 패턴:**

```yaml
name: Terraform Platform
on:
  push:
    branches: [main]
    paths:
      - 'aws-observability-platform/**'
      - '.github/workflows/terraform-platform.yml'
  pull_request:
    paths:
      - 'aws-observability-platform/**'

permissions:
  id-token: write
  contents: read

concurrency:
  group: terraform-platform-${{ github.ref }}
  cancel-in-progress: false

jobs:
  terraform:
    runs-on: ubuntu-latest
    environment: production   # 승인 쓸 때
    defaults:
      run:
        working-directory: aws-observability-platform
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.9.0"
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: ap-northeast-2
      - run: terraform init -input=false
      - run: terraform validate
      - run: terraform plan -out=tfplan -input=false
      # PR이면 여기서 끝 (apply 안 함)
      - if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: terraform apply -input=false -auto-approve tfplan
```

- **PR:** `plan`만, **main 푸시:** `apply` (또는 `workflow_dispatch`로 수동 apply만).
- `backend`를 쓰면 `terraform init`에 `-backend-config` 파일을 붙이거나 환경 변수로 주입.
- 고객 앱은 `working-directory: aws-observability-platform/aws_customer_app` 로 같은 구조의 두 번째 job 또는 두 번째 workflow.

**한 줄 파이프라인으로 묶으려면:**

- Job `platform`: 위와 같이 apply.
- Job `customer` `needs: platform`: credentials 다시, `working-directory` 고객 앱, `terraform init` / `plan` / `apply`.
- `-var="otel_gateway_endpoint=$(terraform -chdir=../ output -raw otel_gateway_nlb_dns):4317"` 처럼 플랫폼 디렉터리에서 output 읽기 (경로는 레이아웃에 맞게 조정; artifact로 NLB DNS 넘기는 방식도 가능).

---

## Phase 6: 첫 수동 검증 (CI 전)

로컬에서 백엔드 연결한 뒤 한 번 성공시키는 것이 좋습니다.

```bash
cd aws-observability-platform
terraform init   # backend 설정 반영 후
terraform plan
terraform apply
terraform output otel_gateway_nlb_dns
cd aws_customer_app
# otel_gateway_endpoint 넣고 apply
```

이게 되면 CI에도 동일 명령만 옮기면 됩니다.

---

## Phase 7: 운영 팁

- `terraform fmt -check` 를 PR job에 넣기.
- 민감 출력: `terraform plan`에 `-no-color`, 로그 마스킹.
- 동시 apply 방지: `concurrency`로 브랜치/스택당 1개.
- ASG + 긴 `user_data`: apply 직후 헬스 안정화 시간을 두고, 고객 앱 apply를 지연시키는 것이 안전할 수 있음.

---

## 요약 체크리스트

1. S3 + DynamoDB state 백엔드 **별도 준비** (버저닝·Lock 테이블)
2. Terraform에 `backend "s3"` (또는 init 시 `-backend-config`) 반영
3. GitHub OIDC용 IAM Role
4. Secrets에 `TF_VAR_*` 및 `AWS_ROLE_ARN` (키 페어 / `ec2_key_path` **불필요**)
5. `.github/workflows`에 platform / customer (또는 통합) 워크플로 추가
6. 로컬로 한 번 end-to-end 성공 후 Actions에 동일 플로우 적용

**SSM 참고:** 인스턴스 접속 문서는 `aws-observability-platform/modules/SSM-MIGRATION.md` 를 보면 됩니다.
