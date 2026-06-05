import {
  createPostJson,
  createProductsBatch,
  createUsersBatch,
  resetAllServices,
  seedProducts,
} from "../lib/k6-common.js";

export function initializePerfRun({
  description,
  orderUrl,
  userUrl,
  productUrl,
  timeout,
  postJson,
  resetTags = { phase: "setup" },
  seedProductsFirst = false,
  userBatch,
  productBatch,
}) {
  // 每次压测开始前都先清空三套服务的数据，避免上一次运行的残留污染结果。
  console.log(`[k6-scenario] ${description}`);

  resetAllServices({
    orderUrl,
    userUrl,
    productUrl,
    timeout,
    tags: resetTags,
  });

  // 某些场景需要先预热 product-service 的基础商品，再写入本轮压测专用商品。
  if (seedProductsFirst) {
    seedProducts(productUrl, timeout);
  }

  const users = userBatch ? createUsersBatch({ userUrl, postJson, ...userBatch }) : [];
  const products = productBatch
    ? createProductsBatch({ productUrl, postJson, ...productBatch })
    : [];

  return {
    users,
    products,
  };
}

export function setupSingleUserAndProductScenario({
  description,
  baseUrl,
  userUrl,
  productUrl,
  timeout,
  emailPrefix,
  userName,
  productPrefix,
  productPrice,
  productStock,
  seedProductsFirst = true,
}) {
  const postJson = createPostJson(timeout);
  const timestamp = Date.now();

  // 热点场景只保留一个用户和一个商品，把竞争集中到同一条库存记录上。
  const initialized = initializePerfRun({
    description,
    orderUrl: baseUrl,
    userUrl,
    productUrl,
    timeout,
    postJson,
    seedProductsFirst,
    userBatch: {
      emailPrefix,
      namePrefix: userName,
      count: 1,
      timestamp,
    },
    productBatch: {
      namePrefix: productPrefix,
      price: productPrice,
      stock: productStock,
      count: 1,
      timestamp,
    },
  });

  const user = initialized.users[0];
  const product = initialized.products[0];

  return {
    user_id: user.id,
    product_id: product.id,
  };
}
