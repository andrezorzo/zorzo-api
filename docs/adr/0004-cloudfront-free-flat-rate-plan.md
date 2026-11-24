---
status: accepted
---

# 0004 · CloudFront flat-rate Free plan for CDN, WAF and DNS

The CloudFront flat-rate Free plan costs US$0 a month with no overage charges and includes the CDN, AWS
WAF with five rules (IP rate limiting, managed rule sets), DDoS protection, a Route 53 hosted zone, the
ACM certificate and 1M requests plus 100 GB a month; requests blocked by the WAF don't count. The domain
stays registered at Registro.br, which delegates its name servers to the Route 53 zone, so the apex can
alias CloudFront. Everything lives in one Terraform.

Discarded: Cloudflare's free plan (free DNS, CDN and WAF too, but it splits the edge across two providers,
or stacks two CDNs in front of S3; kept as the fallback); pay-as-you-go CloudFront with paid AWS WAF
(about US$5 per web ACL, US$1 per rule and per-request fees, beyond the budget); DNS at Registro.br alone
(it can't point the apex at CloudFront: no ALIAS or CNAME flattening, and CloudFront has no fixed IPs).

Cost accepted: the plan's limits shape the design (five cache behaviors, managed origin request policies
only, no CAPTCHA, so Turnstile does that job); sustained traffic above the allowance degrades delivery
instead of billing; AWS can change the plan.

Interview answer: "The free flat-rate plan gives me CDN, WAF and DNS for zero dollars with no surprise
bill. I designed around its limits: one dashboard prefix, Turnstile instead of CAPTCHA. If AWS changes
it, Cloudflare's free plan is the way out."
