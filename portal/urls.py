from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("api/search/", views.search_api, name="search_api"),
]
