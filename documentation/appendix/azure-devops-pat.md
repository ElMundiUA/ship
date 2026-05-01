# Azure DevOps personal access token

**What it is.** A token for the Azure DevOps tracker integration that gives Ship read and write access to work items in your specified project.

**Where to get it.** Sign in at `dev.azure.com`. Click your avatar → "Personal access tokens". Click "New Token", name it "Ship", set scope to "Work Items (read & write)", set an expiration date (Azure defaults to 90 days), and copy the value.

**Where it goes in Ship.** Onboarding wizard, "Workspace tracker" step → Azure DevOps section. You'll fill in three fields: your Azure organization name, your project name, and the personal access token.

**Safety.** Azure DevOps personal access tokens expire by default. Set a calendar reminder to rotate before expiration, or create a new token and update Ship's settings.

Back to [Appendix index](/docs/appendix)
