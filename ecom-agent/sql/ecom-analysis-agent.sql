-- MySQL dump 10.13  Distrib 8.0.45, for Win64 (x86_64)
--
-- Host: 127.0.0.1    Database: ecom-analysis-agent
-- ------------------------------------------------------
-- Server version	8.0.45

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `orders`
--

DROP TABLE IF EXISTS `orders`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `orders` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `order_no` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '订单号',
  `product_id` int NOT NULL COMMENT '商品ID',
  `buyer_name` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '买家昵称',
  `quantity` int NOT NULL DEFAULT '1' COMMENT '购买数量',
  `amount` decimal(10,2) NOT NULL COMMENT '实付金额',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'completed' COMMENT '订单状态',
  `create_time` datetime NOT NULL COMMENT '创建时间',
  `update_time` datetime NOT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_order_no` (`order_no`),
  KEY `idx_orders_product_id` (`product_id`),
  CONSTRAINT `fk_orders_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=50 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `orders`
--
-- ORDER BY:  `id`

LOCK TABLES `orders` WRITE;
/*!40000 ALTER TABLE `orders` DISABLE KEYS */;
INSERT INTO `orders` (`id`, `order_no`, `product_id`, `buyer_name`, `quantity`, `amount`, `status`, `create_time`, `update_time`) VALUES (1,'EO20260000',1,'小明',1,299.00,'completed','2026-10-03 09:16:12','2026-10-04 09:16:12'),(2,'EO20260001',1,'阿花',1,299.00,'completed','2026-10-01 09:16:12','2026-10-02 09:16:12'),(3,'EO20260002',1,'老王',1,299.00,'completed','2026-09-28 09:16:12','2026-09-29 09:16:12'),(4,'EO20260003',1,'甜甜',1,299.00,'completed','2026-09-24 09:16:12','2026-09-25 09:16:12'),(5,'EO20260004',2,'阿杰',1,79.00,'completed','2026-10-04 09:16:12','2026-10-05 09:16:12'),(6,'EO20260005',2,'贝贝',1,79.00,'completed','2026-09-30 09:16:12','2026-10-01 09:16:12'),(7,'EO20260006',2,'大伟',1,79.00,'completed','2026-09-26 09:16:12','2026-09-27 09:16:12'),(8,'EO20260007',3,'圆圆',1,199.00,'completed','2026-10-02 09:16:12','2026-10-03 09:16:12'),(9,'EO20260008',3,'小林',1,199.00,'completed','2026-09-29 09:16:12','2026-09-30 09:16:12'),(10,'EO20260009',3,'静静',1,199.00,'completed','2026-09-21 09:16:12','2026-09-22 09:16:12'),(11,'EO20260010',4,'小明',1,89.00,'completed','2026-10-05 09:16:12','2026-10-06 09:16:12'),(12,'EO20260011',4,'阿花',1,89.00,'completed','2026-09-27 09:16:12','2026-09-28 09:16:12'),(13,'EO20260012',5,'老王',1,159.00,'completed','2026-09-25 09:16:12','2026-09-26 09:16:12'),(14,'EO20260013',5,'甜甜',1,159.00,'completed','2026-09-22 09:16:12','2026-09-23 09:16:12'),(15,'EO20260014',6,'阿杰',1,129.00,'completed','2026-10-03 09:16:12','2026-10-04 09:16:12'),(16,'EO20260015',6,'贝贝',1,129.00,'completed','2026-09-20 09:16:12','2026-09-21 09:16:12'),(17,'EO20260016',7,'大伟',1,99.00,'completed','2026-10-04 09:16:12','2026-10-05 09:16:12'),(18,'EO20260017',7,'圆圆',1,99.00,'completed','2026-09-28 09:16:12','2026-09-29 09:16:12'),(19,'EO20260018',8,'小林',1,39.90,'completed','2026-10-01 09:16:12','2026-10-02 09:16:12'),(20,'EO20260019',8,'静静',1,39.90,'completed','2026-09-23 09:16:12','2026-09-24 09:16:12'),(21,'EO20260020',9,'小明',1,68.00,'completed','2026-10-02 09:16:12','2026-10-03 09:16:12'),(22,'EO20260021',9,'阿花',1,68.00,'completed','2026-09-18 09:16:12','2026-09-19 09:16:12'),(23,'EO20260022',10,'老王',1,249.00,'completed','2026-09-30 09:16:12','2026-10-01 09:16:12'),(24,'EO20260023',10,'甜甜',1,249.00,'completed','2026-09-16 09:16:12','2026-09-17 09:16:12'),(25,'EO20260024',11,'阿杰',1,59.00,'completed','2026-09-29 09:16:12','2026-09-30 09:16:12'),(26,'EO20260025',11,'贝贝',1,59.00,'completed','2026-09-27 09:16:12','2026-09-28 09:16:12'),(27,'EO20260026',12,'大伟',1,189.00,'completed','2026-09-25 09:16:12','2026-09-26 09:16:12'),(28,'EO20260027',12,'圆圆',1,189.00,'completed','2026-09-19 09:16:12','2026-09-20 09:16:12'),(29,'EO20260028',13,'小林',1,69.00,'completed','2026-10-03 09:16:12','2026-10-04 09:16:12'),(30,'EO20260029',13,'静静',1,69.00,'completed','2026-09-24 09:16:12','2026-09-25 09:16:12'),(31,'EO20260030',14,'小明',1,49.00,'completed','2026-10-05 09:16:12','2026-10-06 09:16:12'),(32,'EO20260031',14,'阿花',1,49.00,'completed','2026-09-22 09:16:12','2026-09-23 09:16:12'),(33,'EO20260032',15,'老王',1,119.00,'completed','2026-10-01 09:16:12','2026-10-02 09:16:12'),(34,'EO20260033',15,'甜甜',1,119.00,'completed','2026-09-17 09:16:12','2026-09-18 09:16:12'),(35,'EO20260034',16,'阿杰',1,59.00,'completed','2026-10-04 09:16:12','2026-10-05 09:16:12'),(36,'EO20260035',16,'贝贝',1,59.00,'completed','2026-09-26 09:16:12','2026-09-27 09:16:12'),(37,'EO20260036',17,'大伟',1,89.00,'completed','2026-09-28 09:16:12','2026-09-29 09:16:12'),(38,'EO20260037',17,'圆圆',1,89.00,'completed','2026-09-15 09:16:12','2026-09-16 09:16:12'),(39,'EO20260038',18,'小林',1,159.00,'completed','2026-10-02 09:16:12','2026-10-03 09:16:12'),(40,'EO20260039',18,'静静',1,159.00,'completed','2026-09-21 09:16:12','2026-09-22 09:16:12'),(41,'EO20260040',19,'小明',1,79.00,'completed','2026-09-30 09:16:12','2026-10-01 09:16:12'),(42,'EO20260041',19,'阿花',1,79.00,'completed','2026-09-23 09:16:12','2026-09-24 09:16:12'),(43,'EO20260042',20,'老王',1,399.00,'completed','2026-09-29 09:16:12','2026-09-30 09:16:12'),(44,'EO20260043',20,'甜甜',1,399.00,'completed','2026-09-14 09:16:12','2026-09-15 09:16:12'),(45,'EO20260044',1,'阿杰',1,299.00,'completed','2026-10-05 09:16:12','2026-10-06 09:16:12'),(46,'EO20260045',3,'贝贝',1,199.00,'completed','2026-10-04 09:16:12','2026-10-05 09:16:12'),(47,'EO20260046',5,'大伟',1,159.00,'paid','2026-10-05 09:16:12','2026-10-05 09:16:12'),(48,'EO20260047',12,'圆圆',1,189.00,'shipped','2026-10-03 09:16:12','2026-10-03 09:16:12'),(49,'EO20260048',17,'小林',1,89.00,'cancelled','2026-09-30 09:16:12','2026-09-30 09:16:12');
/*!40000 ALTER TABLE `orders` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `products`
--

DROP TABLE IF EXISTS `products`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `products` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商品名称',
  `category` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '类目',
  `description` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商品描述',
  `price` decimal(10,2) NOT NULL COMMENT '售价',
  `stock` int NOT NULL DEFAULT '0' COMMENT '库存数量',
  `shelf_status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'on_sale' COMMENT '上下架状态',
  `review_status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'normal' COMMENT '人工复核状态',
  `create_time` datetime NOT NULL COMMENT '创建时间',
  `update_time` datetime NOT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=21 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `products`
--
-- ORDER BY:  `id`

LOCK TABLES `products` WRITE;
/*!40000 ALTER TABLE `products` DISABLE KEYS */;
INSERT INTO `products` (`id`, `name`, `category`, `description`, `price`, `stock`, `shelf_status`, `review_status`, `create_time`, `update_time`) VALUES (1,'轻云运动跑鞋','鞋靴','轻量网面跑鞋，适合日常慢跑与通勤，鞋码偏大半码。',299.00,120,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(2,'柔棉短袖T恤','服饰','100% 棉质短袖，多色可选，水洗后可能轻微缩水。',79.00,500,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(3,'静音蓝牙耳机','数码','入耳式蓝牙耳机，支持降噪，续航约 6 小时。',199.00,80,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(4,'保温杯 500ml','家居','不锈钢保温杯，保温 6 小时，杯盖可拆洗。',89.00,200,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(5,'护脊书包','箱包','中学生护脊书包，分区收纳，肩带加宽。',159.00,60,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(6,'厨房刀具套装','家居','三件套不锈钢刀具，含刀架，开刃出厂。',129.00,90,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(7,'儿童益智积木','玩具','200 片积木，适合 3 岁以上，零件较小请家长看护。',99.00,150,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(8,'洗衣液 2L','日用','浓缩洗衣液，低泡易漂，适合机洗。',39.90,300,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(9,'防晒霜 SPF50','美妆','清爽防晒乳，适合油皮，防水防汗。',68.00,180,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(10,'机械键盘青轴','数码','87 键机械键盘，青轴手感，支持键帽热插拔。',249.00,70,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(11,'瑜伽垫 10mm','运动','加厚防滑瑜伽垫，附收纳绑带。',59.00,110,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(12,'不锈钢炒锅 32cm','家居','无涂层炒锅，适合电磁炉与燃气灶。',189.00,45,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(13,'速干运动短裤','服饰','透气速干面料，侧边口袋带拉链。',69.00,220,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(14,'插座排插 6位','家电','六位插孔带独立开关，线长 1.8 米。',49.00,160,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(15,'枕头慢回弹','家居','记忆棉枕头，透气外套可拆洗。',119.00,95,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(16,'无线鼠标静音','数码','2.4G 无线鼠标，静音按键，续航约 12 个月。',59.00,140,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(17,'帆布单肩包','箱包','日系帆布包，内含笔记本隔层。',89.00,75,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(18,'电动牙刷套装','个护','声波电动牙刷，含 2 支刷头，续航约 30 天。',159.00,85,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(19,'桌面小台灯','家居','三档亮度护眼台灯，USB 供电。',79.00,130,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12'),(20,'羽绒服轻薄款','服饰','90% 白鸭绒，可收纳成小袋，尺码偏小建议拍大一码。',399.00,55,'on_sale','normal','2026-08-09 09:16:12','2026-09-08 09:16:12');
/*!40000 ALTER TABLE `products` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `reviews`
--

DROP TABLE IF EXISTS `reviews`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `reviews` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `product_id` int NOT NULL COMMENT '商品ID',
  `order_id` int DEFAULT NULL COMMENT '关联订单ID',
  `rating` int NOT NULL COMMENT '评分1-5',
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '评价正文',
  `img` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '评价图片相对路径',
  `create_time` datetime NOT NULL COMMENT '创建时间',
  `update_time` datetime NOT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_reviews_product_id` (`product_id`),
  KEY `idx_reviews_order_id` (`order_id`),
  CONSTRAINT `fk_reviews_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`),
  CONSTRAINT `fk_reviews_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=47 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='评价表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `reviews`
--
-- ORDER BY:  `id`

LOCK TABLES `reviews` WRITE;
/*!40000 ALTER TABLE `reviews` DISABLE KEYS */;
INSERT INTO `reviews` (`id`, `product_id`, `order_id`, `rating`, `content`, `img`, `create_time`, `update_time`) VALUES (1,1,1,5,'鞋子很轻，跑步不累脚，推荐。',NULL,'2026-10-05 09:16:12','2026-10-05 09:16:12'),(2,1,2,2,'尺码明显偏大，按平时码拍了结果松垮，客服让我自己垫鞋垫。',NULL,'2026-10-03 09:16:12','2026-10-03 09:16:12'),(3,1,3,1,'鞋底开胶了，穿了两周就这样，质量太差。','/uploads/review_samples/shoe_glue-demo.jpg','2026-09-30 09:16:12','2026-09-30 09:16:12'),(4,1,4,3,'外观还可以，但是鞋垫偏硬，走久了脚疼。',NULL,'2026-09-26 09:16:12','2026-09-26 09:16:12'),(5,2,5,5,'面料舒服，洗了不变形。',NULL,'2026-10-06 09:16:12','2026-10-06 09:16:12'),(6,2,6,2,'缩水严重，洗一次短了一截，差评。',NULL,'2026-10-02 09:16:12','2026-10-02 09:16:12'),(7,2,7,4,'颜色和图片差不多，性价比不错。',NULL,'2026-09-28 09:16:12','2026-09-28 09:16:12'),(8,3,8,1,'右耳没有声音，怀疑是次品。','/uploads/review_samples/earphone_broken-demo.jpg','2026-10-04 09:16:12','2026-10-04 09:16:12'),(9,3,9,2,'降噪一般，地铁里基本听不清。',NULL,'2026-10-01 09:16:12','2026-10-01 09:16:12'),(10,3,10,5,'音质超出预期，续航也够用。',NULL,'2026-09-23 09:16:12','2026-09-23 09:16:12'),(11,4,11,4,'保温效果可以，出门带水方便。',NULL,'2026-10-07 09:16:12','2026-10-07 09:16:12'),(12,4,12,1,'收到时杯盖裂了，包装也有压痕。','/uploads/review_samples/broken_box-demo.jpg','2026-09-29 09:16:12','2026-09-29 09:16:12'),(13,5,13,5,'背着挺舒服，孩子说分区好用。',NULL,'2026-09-27 09:16:12','2026-09-27 09:16:12'),(14,5,14,2,'拉链很容易卡住，质量一般。',NULL,'2026-09-24 09:16:12','2026-09-24 09:16:12'),(15,6,15,3,'刀还行，就是刀架塑料感太重。',NULL,'2026-10-05 09:16:12','2026-10-05 09:16:12'),(16,6,16,1,'刀刃有缺口，感觉是返修货。',NULL,'2026-09-22 09:16:12','2026-09-22 09:16:12'),(17,7,17,5,'孩子很喜欢，拼了好久。',NULL,'2026-10-06 09:16:12','2026-10-06 09:16:12'),(18,7,18,2,'少了好几块零件，联系客服补发很慢。',NULL,'2026-09-30 09:16:12','2026-09-30 09:16:12'),(19,8,19,4,'洗净效果不错，味道清淡。',NULL,'2026-10-03 09:16:12','2026-10-03 09:16:12'),(20,8,20,5,'比超市便宜，会回购。',NULL,'2026-09-25 09:16:12','2026-09-25 09:16:12'),(21,9,21,2,'涂完闷痘，油皮慎入。',NULL,'2026-10-04 09:16:12','2026-10-04 09:16:12'),(22,9,22,5,'不油腻，户外一天没有晒黑。',NULL,'2026-09-20 09:16:12','2026-09-20 09:16:12'),(23,10,23,1,'有好几个键失灵，退货中。',NULL,'2026-10-02 09:16:12','2026-10-02 09:16:12'),(24,10,24,4,'青轴清脆，打字爽。',NULL,'2026-09-18 09:16:12','2026-09-18 09:16:12'),(25,11,25,5,'厚度够，跪姿也不疼。',NULL,'2026-10-01 09:16:12','2026-10-01 09:16:12'),(26,11,26,3,'味道有点大，晾了两天才好。',NULL,'2026-09-29 09:16:12','2026-09-29 09:16:12'),(27,12,27,2,'手柄焊接处有毛刺，割手。',NULL,'2026-09-27 09:16:12','2026-09-27 09:16:12'),(28,12,28,5,'炒菜受热均匀，好用。',NULL,'2026-09-21 09:16:12','2026-09-21 09:16:12'),(29,13,29,4,'透气好，跑步出汗也能干得快。',NULL,'2026-10-05 09:16:12','2026-10-05 09:16:12'),(30,13,30,1,'尺码标错了，XL 穿起来像 M。',NULL,'2026-09-26 09:16:12','2026-09-26 09:16:12'),(31,14,31,5,'插孔够用，线也不短。',NULL,'2026-10-07 09:16:12','2026-10-07 09:16:12'),(32,14,32,2,'有一个开关按下去弹不起来。',NULL,'2026-09-24 09:16:12','2026-09-24 09:16:12'),(33,15,33,4,'睡着很舒服，回弹慢。',NULL,'2026-10-03 09:16:12','2026-10-03 09:16:12'),(34,15,34,1,'拆开有股刺鼻味道，怀疑不合格。',NULL,'2026-09-19 09:16:12','2026-09-19 09:16:12'),(35,16,35,5,'静音效果真好，办公室用很合适。',NULL,'2026-10-06 09:16:12','2026-10-06 09:16:12'),(36,16,36,3,'偶发卡顿，换电池后好了。',NULL,'2026-09-28 09:16:12','2026-09-28 09:16:12'),(37,17,37,4,'容量大，通勤够用。',NULL,'2026-09-30 09:16:12','2026-09-30 09:16:12'),(38,17,38,2,'肩带缝线开了，用了不到一个月。',NULL,'2026-09-17 09:16:12','2026-09-17 09:16:12'),(39,18,39,5,'刷牙干净，震动力度合适。',NULL,'2026-10-04 09:16:12','2026-10-04 09:16:12'),(40,18,40,1,'充电座接触不良，经常充不进电。',NULL,'2026-09-23 09:16:12','2026-09-23 09:16:12'),(41,19,41,4,'亮度够，夜间看书写作业可以。',NULL,'2026-10-02 09:16:12','2026-10-02 09:16:12'),(42,19,42,2,'按钮松动，一碰就自己切换档位。',NULL,'2026-09-25 09:16:12','2026-09-25 09:16:12'),(43,20,43,3,'保暖可以，但袖子偏短。',NULL,'2026-10-01 09:16:12','2026-10-01 09:16:12'),(44,20,44,1,'充绒不均匀，一边厚一边薄。',NULL,'2026-09-16 09:16:12','2026-09-16 09:16:12'),(45,1,45,2,'最近这双鞋差评好像变多了，我也中招，鞋帮磨脚。',NULL,'2026-10-07 09:16:12','2026-10-07 09:16:12'),(46,3,46,1,'包装破损，耳机仓有明显划痕。','/uploads/review_samples/package_scratch-demo.jpg','2026-10-06 09:16:12','2026-10-06 09:16:12');
/*!40000 ALTER TABLE `reviews` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `username` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '账号',
  `password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '密码',
  `name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '名称',
  `role` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '角色：user-普通用户，admin-管理员',
  `email` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '邮箱',
  `phone` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '手机号',
  `avatar` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '头像',
  `status` int NOT NULL COMMENT '状态：0-禁用，1-正常',
  `create_time` datetime NOT NULL COMMENT '创建时间',
  `update_time` datetime NOT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE KEY `username` (`username`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='用户信息表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `users`
--
-- ORDER BY:  `id`

LOCK TABLES `users` WRITE;
/*!40000 ALTER TABLE `users` DISABLE KEYS */;
INSERT INTO `users` (`id`, `username`, `password`, `name`, `role`, `email`, `phone`, `avatar`, `status`, `create_time`, `update_time`) VALUES (1,'admin','$2b$12$ypJClLKejFCsyn2eltOco.YAmXory0HFjh7E/oHu9MBzDZZ6wYSSi','管理员','admin','admin@system.cn','13988776699','/uploads/1790046856623_dc4830aa.jpg',1,'2026-09-11 15:30:07','2026-09-22 11:14:34'),(2,'aaa','$2a$10$phJ2k4sP5.c7T/LuXwzyTu/AlbbOFXblsAZEW6mIfh3koVxQ3Hhny','小白','user','xiaobai@qq.com','13899776688','/uploads/1790046891012_452a9884.png',1,'2026-09-09 16:56:41','2026-09-22 11:14:52'),(3,'bbb','$2a$10$blJ4Qgbaqxm5jmUAWvuiRuKy/FiqbmFv.n1i0noM3pxUZrYhd2HNK','小黑','user','xiaohei@163.com','13799886699','/uploads/1790046917012_b9ee534a.png',1,'2026-09-22 11:15:00','2026-09-22 11:15:30');
/*!40000 ALTER TABLE `users` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Dumping events for database 'ecom-analysis-agent'
--

--
-- Dumping routines for database 'ecom-analysis-agent'
--
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-10-22 11:33:49
