import request from '@/utils/request'

/**
 * 分页模糊查询商品列表
 */
export function getProductPageList(params) {
  return request({
    url: '/api/ecommerce/products',
    method: 'get',
    params
  })
}

/**
 * 分页模糊查询订单列表
 */
export function getOrderPageList(params) {
  return request({
    url: '/api/ecommerce/orders',
    method: 'get',
    params
  })
}

/**
 * 分页模糊查询评价列表
 */
export function getReviewPageList(params) {
  return request({
    url: '/api/ecommerce/reviews',
    method: 'get',
    params
  })
}

/** 查询单个商品详情，报告和预警由后端按当前登录用户过滤。 */
export function getProductDetail(id) {
  return request.get(`/api/ecommerce/products/${id}/detail`)
}

/** 查询订单及其关联评价；没有评价时review为null。 */
export function getOrderDetail(id) {
  return request.get(`/api/ecommerce/orders/${id}/detail`)
}
