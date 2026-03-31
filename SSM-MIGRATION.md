# 키 페어 제거 · Session Manager(SSM) 전환

EC2 키 페어 대신 **AWS Systems Manager Session Manager**로 접속하도록 변경한 내역입니다. SSH(22) 인바운드는 제거했습니다.

---

## 요약

| 항목 | 내용 |
|------|------|
| 접속 방식 | SSH + `.pem` 키 → **Session Manager** (`aws ssm start-session` 또는 콘솔 Connect) |
| IAM | 인스턴스 프로파일에 **`AmazonSSMManagedInstanceCore`** 정책 연결 |
| 보안 그룹 | **TCP 22 인바운드 규칙 삭제** (플랫폼 게이트웨이 SG, 고객 앱 frontend/backend SG) |
| Terraform 변수 | `ec2_key_name`, `ec2_key_path` 제거 |

**전제:** 인스턴스는 SSM Agent가 동작하고(우분투 공식 AMI 등), 아웃바운드로 AWS API(SMM) 도달 가능해야 합니다. **운영자 IAM**에는 `ssm:StartSession` 등 Session Manager 권한이 필요합니다.

---

## `modules/compute/`

| 파일 | 변경 내용 |
|------|-----------|
| `main.tf` | `aws_iam_role_policy_attachment.otel_collector_ssm` 추가 (`AmazonSSMManagedInstanceCore`). `aws_launch_template.otel_gateway`에서 **`key_name` 제거**. |
| `variables.tf` | **`ec2_key_name`**, **`ec2_key_path`** 변수 삭제. |
| `outputs.tf` | `otel_collector_public_ip` 설명을 Session Manager 안내로 수정. |

---

## `modules/networking/`

| 파일 | 변경 내용 |
|------|-----------|
| `main.tf` | `aws_security_group.otel_collector`에서 **SSH(22) 인바운드** 블록 전체 삭제. |

---

## 플랫폼 루트 (`aws-observability-platform/`)

| 파일 | 변경 내용 |
|------|-----------|
| `main.tf` | `module.compute`에 넘기던 **`ec2_key_name`**, **`ec2_key_path`** 인자 제거. |
| `variables.tf` | **`ec2_key_name`**, **`ec2_key_path`** 변수 삭제. |
| `outputs.tf` | **`ssh_command`** 제거 → **`ssm_gateway_hint`** 추가 (instance-id 조회 + `ssm start-session` 예시). **`next_steps`** 출력에서 SSH 문구를 Session Manager 기준으로 수정. |
| `terraform.tfvars.example` | 키 페어 관련 주석·**`ec2_key_name`** 항목 제거, AMI 주석 보강. |

---

## `aws_customer_app/` (별도 Terraform 루트)

| 파일 | 변경 내용 |
|------|-----------|
| `main.tf` | **`aws_iam_role.customer_ec2`**, **`aws_iam_role_policy_attachment.customer_ec2_ssm`**, **`aws_iam_instance_profile.customer_ec2`** 추가. `frontend`/`backend` **`aws_instance`**에 **`iam_instance_profile`** 지정, **`key_name` 제거**. **`aws_security_group` frontend/backend**에서 **SSH(22) 인바운드** 삭제. |
| `variables.tf` | **`ec2_key_name`** 변수 삭제. |
| `outputs.tf` | **`ssh_frontend`**, **`ssh_backend`** 제거 → **`ssm_connect_frontend`**, **`ssm_connect_backend`** (`aws ssm start-session --target <id> ...`). |
| `terraform.tfvars.example` | **`ec2_key_name`** 줄 제거. |

---

## 마이그레이션 시 참고

- 로컬 **`terraform.tfvars`**에 예전에 넣어 둔 **`ec2_key_name` / `ec2_key_path`**가 있으면 **삭제**해야 `terraform plan`이 통과합니다.
- 이미 키 페어로 생성된 EC2는 `terraform apply`로 **Launch Template / 인스턴스**가 갱신되며 키 없이 재생성될 수 있습니다. **다운타임·교체** 계획을 함께 검토하세요.

---

## 접속 예시

**플랫폼 게이트웨이 (ASG)**

```bash
terraform output ssm_gateway_hint
# 출력된 describe-instances로 instance-id 확인 후
aws ssm start-session --target <instance-id> --region ap-northeast-2
```

**고객 앱**

```bash
cd aws_customer_app
terraform output ssm_connect_frontend
terraform output ssm_connect_backend
```
