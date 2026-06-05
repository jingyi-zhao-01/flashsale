# Flashsale Architecture

These diagrams describe the flashsale system at the application and domain level only.

- No Kubernetes topology
- No Grafana provisioning
- No deployment-plane details

<table>
  <tr>
    <td width="33%" valign="top">
      <h3>1. C4 Container</h3>
      <p>Core runtime building blocks and their dependencies.</p>
      <p>
        <img
          src="./docs/diagram/flashsale-c4-container.svg"
          alt="Flashsale C4 Container Diagram"
          width="100%"
        />
      </p>
      <p><strong>Includes</strong></p>
      <ul>
        <li>client</li>
        <li>user-service</li>
        <li>order-service</li>
        <li>product-service</li>
        <li>order and product PostgreSQL</li>
        <li>order terminalization queue</li>
      </ul>
      <p>
        Source:
        <a href="/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/docs/diagram/flashsale-c4-container.d2">flashsale-c4-container.d2</a>
      </p>
    </td>
    <td width="33%" valign="top">
      <h3>2. Runtime Flow</h3>
      <p>How one order request moves through the system.</p>
      <p>
        <img
          src="./docs/diagram/flashsale-runtime-order-flow.svg"
          alt="Flashsale Runtime Order Flow"
          width="100%"
        />
      </p>
      <p><strong>Highlights</strong></p>
      <ul>
        <li>synchronous validation and reservation</li>
        <li>order persistence</li>
        <li>queue enqueue</li>
        <li>async confirm / cancel worker path</li>
      </ul>
      <p>
        Source:
        <a href="/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/docs/diagram/flashsale-runtime-order-flow.d2">flashsale-runtime-order-flow.d2</a>
      </p>
    </td>
    <td width="33%" valign="top">
      <h3>3. State Machines</h3>
      <p>The lifecycle models that define correctness boundaries.</p>
      <p>
        <img
          src="./docs/diagram/flashsale-state-machines.svg"
          alt="Flashsale State Machines"
          width="100%"
        />
      </p>
      <p><strong>Covers</strong></p>
      <ul>
        <li>order state machine</li>
        <li>reservation state machine</li>
        <li>terminalization task state machine</li>
      </ul>
      <p>
        Source:
        <a href="/home/jingyi/PycharmProjects/homelab-cloud/application/flashsale/docs/diagram/flashsale-state-machines.d2">flashsale-state-machines.d2</a>
      </p>
    </td>
  </tr>
</table>
