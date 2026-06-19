FROM quay.io/keycloak/keycloak:26.6.2

COPY keycloak-theme-for-kc-22-to-25.jar /opt/keycloak/providers/

# re-augment the build to let the operator's --optimized start works
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
