from django.contrib.auth import get_user_model
from django.test import TestCase


class AccountPageTests(TestCase):
    def test_login_and_signup_pages_render(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)
        self.assertEqual(self.client.get("/accounts/signup/").status_code, 200)
        self.assertEqual(self.client.get("/accounts/forgot-password/").status_code, 200)

    def test_dashboard_requires_authentication(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_authenticated_dashboard_renders(self):
        user = get_user_model().objects.create_user(username="demo", password="Roster-Preview-482!")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Import a previous roster")
