# Security Policy

## Supported Versions

Currently, the `main` branch is the only version receiving security updates.

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |
| < 1.0   | :x:                |

## Scope & Covered Subsystems

We take the security of this aerospace suite very seriously. This policy covers:
- **RF & DSP Pipelines** (Buffer overflows, IQ validation, memory management)
- **Telemetry Parsers** (Deframer limits, malformed CSP/AX.25 packet handling)
- **Cryptography & PKI** (ECDSA SECP256R1 keys, XTEA in CTR mode, signature verification)

## Reporting a Vulnerability

We recommend reporting vulnerabilities privately via **security@dynamix-labs.org**. 

We aim to acknowledge reports within **72 hours** and provide a remediation plan or patch as soon as possible. Please do not open a public issue for critical security vulnerabilities, especially those related to cryptographic bypassing or ground-station network compromises.

## Secret Hygiene & Best Practices

Contributors must ensure that:
1. No raw IQ captures containing sensitive or live API keys are ever committed.
2. Debug logs are kept high-level; **keys, IVs, and decrypted sensitive telemetry must never be printed to stdout**.
3. All satellite credentials and federated node keys must be managed via GitHub Actions secrets in CI.
