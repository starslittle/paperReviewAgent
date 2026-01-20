"""
MinerU PDF 提取脚本 - 最终完美版
集成：混合网络库 + 批量上传接口 + 批量查询接口
"""

import argparse
import glob
import logging
import os
import time
import zipfile
import io
import shutil
import json
from dotenv import load_dotenv

# 【关键】导入两个库，分别命名
try:
    # 用于 API 交互 (伪装能力强，能过 MinerU 的 WAF)
    from curl_cffi import requests as cffi_requests

    # 用于文件上传 (兼容性强，解决 403 签名和 DNS 解析问题)
    import requests as std_requests
except ImportError:
    print("错误: 缺少必要库，请执行: pip install curl_cffi requests")
    exit(1)

# 加载环境变量
load_dotenv()
if not os.getenv("MINERU_API_TOKEN"):
    load_dotenv("../.env")

# 初始化日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class MinerUExtractor:
    def __init__(self, api_token=None, base_url=None):
        self.api_token = api_token or os.getenv("MINERU_API_TOKEN")
        self.base_url = (
            base_url or os.getenv("MINERU_API_BASE_URL", "https://mineru.net/api/v4")
        ).rstrip("/")

        # 创建 curl_cffi Session (用于 API 请求)
        self.session = cffi_requests.Session()
        self.impersonate = "chrome120"

        # 设置 API 请求头
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Authorization": f"Bearer {self.api_token}",
            }
        )

        if not self.api_token:
            logger.error("未找到 MINERU_API_TOKEN")
            exit(1)

    def extract_pdf(self, pdf_path, sid, result_dir):
        # 修改后的输出目录：./extract_output/{sid}/MinerU/
        output_dir = os.path.join(result_dir, sid, "MinerU")
        os.makedirs(output_dir, exist_ok=True)

        # 检查本地是否已有结果
        if os.path.exists(os.path.join(output_dir, "middle.json")):
            logger.info(f"[MinerU] 文档 {sid} 已处理过，跳过")
            return True

        # 1. 提交任务 (申请链接 -> 标准requests上传)
        batch_id = self._submit_task(pdf_path)
        if not batch_id:
            return False

        # 2. 等待完成 (使用批量查询接口)
        result_url = self._wait_for_completion(batch_id)
        if not result_url:
            return False

        # 3. 下载结果
        success = self._download_and_extract(result_url, output_dir)
        if success:
            logger.info(f"[✓] 文档 {sid} 处理完成")
        return success

    def _submit_task(self, pdf_path):
        """
        步骤一：提交任务
        1. 使用 cffi 申请上传链接
        2. 使用 std_requests 上传文件 (避免 403/DNS 问题)
        """
        apply_url = f"{self.base_url}/file-urls/batch"
        filename = os.path.basename(pdf_path)

        try:
            logger.info(f"[→] 正在申请上传链接: {filename}")

            payload = {"files": [{"name": filename, "data_id": "doc_1"}]}

            # 1. 申请链接
            resp_apply = self.session.post(
                apply_url, json=payload, impersonate=self.impersonate, timeout=30
            )

            if resp_apply.status_code != 200:
                logger.error(f"[✗] 申请链接失败: {resp_apply.text}")
                return None

            res_json = resp_apply.json()
            if res_json.get("code") != 0:
                logger.error(f"[✗] API拒绝申请: {res_json.get('msg')}")
                return None

            batch_id = res_json["data"]["batch_id"]
            upload_url = res_json["data"]["file_urls"][0]

            logger.info(f"[→] 链接申请成功，正在上传文件数据...")

            # 2. 上传文件 (使用标准 requests，不带额外 Header)
            with open(pdf_path, "rb") as f:
                file_content = f.read()

                # 直接 PUT 二进制，timeout 设置长一点
                resp_upload = std_requests.put(
                    upload_url, data=file_content, timeout=300
                )

            if resp_upload.status_code != 200:
                logger.error(f"[✗] 文件上传失败 HTTP {resp_upload.status_code}")
                # 截取少量错误信息避免刷屏
                logger.error(f"    详情: {resp_upload.text[:200]}")
                return None

            logger.info(f"[✓] 文件上传成功! Batch ID: {batch_id}")
            return batch_id

        except Exception as e:
            logger.error(f"[✗] 提交异常: {e}")
            return None

    def _wait_for_completion(self, batch_id, max_wait_time=600):
        """
        修正版：适配批量接口的 state 字段和 done 状态
        """
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        start_time = time.time()
        logger.info(f"[⏳] 开始轮询任务状态 (Batch ID: {batch_id})...")

        while (time.time() - start_time) < max_wait_time:
            try:
                response = self.session.get(
                    url, impersonate=self.impersonate, timeout=20
                )

                if response.status_code != 200:
                    logger.warning(f"查询状态失败 HTTP {response.status_code}")
                    time.sleep(5)
                    continue

                res_json = response.json()
                data = res_json.get("data", {})

                # 1. 提取结果列表
                # 官方文档显示字段为 extract_result
                results = data.get("extract_result", [])
                if not results:
                    # 兼容旧版本可能的字段 infos
                    results = data.get("count", [])

                if not results:
                    # 可能服务器还没来得及生成列表
                    time.sleep(5)
                    continue

                # 我们只传了1个文件，取第1个
                item = results[0]

                # 2. 【核心修正】获取状态字段
                # 优先找 state，找不到再找 status
                state = item.get("state") or item.get("status")

                # 3. 【核心修正】判断状态值
                # 成功状态是 'done' 或 'success'
                if state in ["done", "success"]:
                    # 获取下载链接 (优先找 full_zip_url)
                    download_url = item.get("full_zip_url") or item.get("full_res")
                    logger.info(f"[✓] 解析成功")
                    return download_url

                elif state in ["failed", "error"]:
                    error_msg = item.get("error_msg", "未知错误")
                    logger.error(f"[✗] 解析失败: {error_msg}")
                    return None

                else:
                    # 状态可能是: processing, pending, waiting-file, running, converting
                    elapsed = int(time.time() - start_time)
                    if elapsed % 10 == 0:
                        # 打印当前状态，方便调试
                        logger.info(f"[⏳] 解析中... (当前状态: {state})")
                    time.sleep(5)

            except Exception as e:
                logger.warning(f"轮询异常: {e}")
                time.sleep(5)

        logger.error(f"[✗] 任务等待超时 ({max_wait_time}秒)")
        return None

    def _download_and_extract(self, result_url, output_dir):
        """
        步骤三：下载并解压结果 (调试版：打印所有文件)
        """
        try:
            logger.info("[↓] 正在下载结果包...")
            response = self.session.get(
                result_url, impersonate=self.impersonate, timeout=120
            )

            if response.status_code != 200:
                logger.error(f"[✗] 下载失败 HTTP {response.status_code}")
                return False

            # 解压
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                z.extractall(output_dir)

            # =================================================
            # 【调试代码】打印解压出来的所有文件，看看叫什么名字
            # =================================================
            logger.info(f"📂 调试：正在扫描目录 {output_dir} ...")
            file_list = []
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, output_dir)
                    file_list.append(rel_path)
                    logger.info(f"   📄 发现文件: {rel_path}")
            # =================================================

            # 寻找目标 JSON 文件
            # MinerU 新版可能把 output 改名了，比如叫 model.json 或 content_list.json
            target_file_name = "middle.json"
            found_json = None

            for f in file_list:
                # 优先找 middle.json
                if f.endswith("middle.json"):
                    found_json = f
                    break
                # 兼容：如果找不到 middle，找找有没有叫 model.json 的
                if f.endswith("model.json"):
                    found_json = f

            if found_json:
                src_path = os.path.join(output_dir, found_json)
                dst_path = os.path.join(
                    output_dir, "middle.json"
                )  # 统一重命名为 middle.json

                # 如果文件不在根目录，或者是别的名字，移动并重命名
                if src_path != dst_path:
                    if os.path.exists(dst_path):
                        os.remove(dst_path)
                    shutil.move(src_path, dst_path)
                    logger.info(f"[✓] 已将 {found_json} 移动并重命名为 middle.json")
                return True
            else:
                logger.warning(
                    f"[!] 未找到 middle.json 或 model.json，请检查上方打印的文件列表"
                )

            return False

        except Exception as e:
            logger.error(f"下载解压失败: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(description="MinerU PDF 提取脚本")
    parser.add_argument(
        "--raw-data-dir", default="../data/", help="原始数据目录"
    )
    parser.add_argument(
        "--result-dir", default="./extract_output/", help="输出结果目录"
    )
    parser.add_argument("--doc-id", type=str, default=None, help="指定处理的文档 ID")
    args = parser.parse_args()

    os.makedirs(args.result_dir, exist_ok=True)
    extractor = MinerUExtractor()

    search_path = os.path.join(args.raw_data_dir, "*")
    pdf_count = 0

    all_items = glob.glob(search_path)
    for item in all_items:
        if not os.path.isdir(item):
            continue
        sid = os.path.basename(item)
        if args.doc_id and sid != args.doc_id:
            continue

        # 查找PDF
        pdf_list = glob.glob(os.path.join(item, "*.pdf"))
        if not pdf_list:
            if os.path.exists(os.path.join(item, "document.pdf")):
                pdf_list = [os.path.join(item, "document.pdf")]
            else:
                continue

        pdf_count += 1
        logger.info(f"\n{'='*60}")
        logger.info(f"[{pdf_count}] 处理文档: {sid}")
        logger.info(f"{'='*60}")
        extractor.extract_pdf(pdf_list[0], sid, args.result_dir)

    logger.info(f"\n[✓] 全部处理完成，共处理 {pdf_count} 个文档")


if __name__ == "__main__":
    main()
