# Load Balancing Options

This document outlines the available load balancing options for the Platform application deployment.

## Default Option: AWS Application Load Balancer (ALB)

The default load balancing solution uses AWS Application Load Balancer through the AWS Load Balancer Controller.

### ALB Annotations Example in values file

```yaml
ingress:
  enabled: true
  className: "alb"  
  hostname: itential.example.com
  annotations:
    alb.ingress.kubernetes.io/backend-protocol: "HTTPS"
    alb.ingress.kubernetes.io/healthcheck-path: "/health/status?exclude-services=true"
    alb.ingress.kubernetes.io/healthcheck-port: "3443"
    alb.ingress.kubernetes.io/healthcheck-protocol: "HTTPS"
    alb.ingress.kubernetes.io/healthcheck-interval-seconds: "15"
    alb.ingress.kubernetes.io/healthcheck-timeout-seconds: "5"
    alb.ingress.kubernetes.io/healthy-threshold-count: "2"
    # Include port 8080 if using Gateway Manager with IAG5, otherwise remove it
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS": 443},{"HTTPS": 8080}]'
    alb.ingress.kubernetes.io/load-balancer-attributes: idle_timeout.timeout_seconds=60
    alb.ingress.kubernetes.io/load-balancer-name: "itential-example-lb"
    alb.ingress.kubernetes.io/scheme: "internet-facing"
    alb.ingress.kubernetes.io/success-codes: "200"
    alb.ingress.kubernetes.io/target-type: "ip"
    alb.ingress.kubernetes.io/unhealthy-threshold-count: "2"
    alb.ingress.kubernetes.io/websocket-paths: "/ws"
    alb.ingress.kubernetes.io/target-group-attributes: stickiness.enabled=true,stickiness.lb_cookie.duration_seconds=3600
```

## Alternative Option: NGINX Ingress Controller

For environments where ALB is not available or preferred, NGINX Ingress Controller can be used as an alternative.

### NGINX Annotations Example

```yaml
ingress:
  enabled: true
  className: "nginx"  
  hostname: itential.example.com
  annotations:    
    nginx.ingress.kubernetes.io/backend-protocol: "HTTPS"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/use-regex: "true"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "60"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "60"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "60"
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
    nginx.ingress.kubernetes.io/affinity: "cookie"
    nginx.ingress.kubernetes.io/affinity-mode: "persistent"
    nginx.ingress.kubernetes.io/session-cookie-name: "iap-server"
    nginx.ingress.kubernetes.io/session-cookie-max-age: "3600"
    nginx.ingress.kubernetes.io/websocket-services: "iap-service"

```

## Configuration Selection

The load balancer type can be configured in the values file. See the ingress section:

```yaml
ingress:
  enabled: true
  className: "alb"  # Change to "nginx" for NGINX ingress
  hostname: itential.example.com
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: "HTTPS"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
```

## Key Differences

| Feature | ALB | NGINX |
|---------|-----|-------|
| **Provider** | AWS Native | Third-party |
| **SSL Termination** | At load balancer | At load balancer or pod |
| **WebSocket Support** | Native | Requires annotation |
| **Session Affinity** | Target group level | Cookie-based |
| **Health Checks** | Advanced AWS health checks | HTTP/HTTPS probes |