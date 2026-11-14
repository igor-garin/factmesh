# Security

factmesh takes untrusted input from the network and passes results to AI
agents, so security reports are the highest priority.

## Reporting a vulnerability

Use GitHub's private reporting: the **Security** tab of this repository,
then **Report a vulnerability**. Please do not open a public issue.

Especially wanted:

- a claim that passes its check but misstates what the check confirms
- publisher-written text reaching the agent outside the check fields
- a check that contacts a host or address it should not
- input that crashes or stalls a receiver
- a way to suppress or alter another publisher's claims

## Scope and known limits

Known limits are documented in
[docs/hostile-tests.md](docs/hostile-tests.md#risks-that-remain). Reports that
show one of them is worse than described there are welcome.
