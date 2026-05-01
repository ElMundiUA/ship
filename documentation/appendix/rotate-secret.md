# What does "rotate a secret" mean and when do I do it?

Rotating a secret means generating a new value and replacing the old one everywhere it's used. You rotate a secret when: a team member leaves (their PAT becomes a liability because it still carries their identity and permissions even after they're gone), a token expires (some providers like Atlassian and Azure DevOps issue tokens with a time limit—you have to rotate before they stop working), or a value might have been exposed (someone screenshotted a config file, a laptop was stolen, a person with access to the value has left the team).

The order matters: revoke or disable the old value on the provider first, then generate a new one and paste it into Ship's form. If you do it the other way around—paste the new value into Ship, then revoke the old one—there's a window where both values are valid, and an attacker who already has the old one can still use it. Best practice is to revoke first, verify that the old token no longer works on the provider's side, generate the new one, test it, and then paste it into Ship.

Back to [Appendix index](/docs/appendix)
