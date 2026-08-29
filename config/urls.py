from django.contrib import admin
from django.urls import include, path

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)


urlpatterns = [
    path("admin/", admin.site.urls),

    # JWT auth endpoints (unversioned - these issue/refresh
    # tokens rather than serving resources, so they sit outside
    # the versioned API surface). Must come BEFORE the
    # api/<str:version>/ pattern below, since <str:version>
    # would otherwise greedily match "token" as a version
    # string and swallow these requests with a 404.
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/token/verify/", TokenVerifyView.as_view(), name="token_verify"),

    # Versioned REST API - /api/v1/customers/, /api/v1/accounts/, etc.
    # URLPathVersioning reads the captured "version" segment and
    # validates it against REST_FRAMEWORK["ALLOWED_VERSIONS"].
    path("api/<str:version>/", include("accounts.api_urls")),

    path("api-auth/", include("rest_framework.urls")),
    path("", include("accounts.urls")),
]