"""
MinerU 统一文件提取脚本 (step1_extract)
功能：使用 MinerU API 解析文档，提取文本、字体、布局等信息。
支持：PDF、DOC、DOCX（同一链路，API 见 https://mineru.net/apiManage/docs ）
"""

import argparse
import glob
import hashlib
import json
import logging
import os
import time
import zipfile
import io
import shutil
from dotenv import load_dotenv

# 导入必要的库
try:
    # 用于 API 交互
    from curl_cffi import requests as cffi_requests

    # 用于文件上传
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
            logger.error("未找到 MINERU_API_TOKEN，请检查环境变量配置")
            exit(1)

    def extract_file(self, file_path, sid, result_dir):
        """
        使用 MinerU API 提取文档内容（PDF/DOC/DOCX 等）

        Args:
            file_path: 文档文件路径
            sid: 文档 ID
            result_dir: 结果输出根目录

        Returns:
            bool: 是否成功
        """
        # 输出目录：preprocess/extract_output/MinerU/{sid}/
        output_dir = os.path.join(result_dir, "MinerU", sid)
        os.makedirs(output_dir, exist_ok=True)

        # 检查本地是否已有结果（兼容 content_list/layout/middle）
        if self._has_existing_outputs(output_dir):
            self._canonicalize_outputs(output_dir)
            logger.info(f"[MinerU] 文档 {sid} 已处理过，跳过")
            return True

        # 1. 提交任务 (申请链接 -> 上传文件)
        batch_id = self._submit_task(file_path)
        if not batch_id:
            return False

        # 2. 等待完成 (使用批量查询接口)
        result_url = self._wait_for_completion(batch_id)
        if not result_url:
            return False

        # 3. 下载结果
        success = self._download_and_extract(result_url, output_dir)
        if success:
            self._canonicalize_outputs(output_dir)
            logger.info(f"[✓] 文档 {sid} 处理完成")
        return success

    def _has_existing_outputs(self, output_dir: str) -> bool:
        if os.path.exists(os.path.join(output_dir, "middle.json")):
            return True
        if os.path.exists(os.path.join(output_dir, "layout.json")):
            return True
        if os.path.exists(os.path.join(output_dir, "content_list.json")):
            return True
        for name in os.listdir(output_dir):
            if name.endswith("_content_list.json"):
                return True
        return False

    def _file_md5(self, path: str) -> str:
        md5 = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                md5.update(chunk)
        return md5.hexdigest()

    def _canonicalize_outputs(self, output_dir: str) -> None:
        """
        将 MinerU 输出标准化，减少后续 step2 的不确定性：
        1) 多个 *_content_list.json 时固定生成 content_list.json
        2) model.json 回退到 middle.json
        3) 产出 extract_manifest.json 记录源文件与 hash
        """
        manifest = {
            "output_dir": output_dir,
            "content_list_candidates": [],
            "selected_content_list": "",
            "selected_content_list_hash": "",
            "layout_exists": os.path.exists(os.path.join(output_dir, "layout.json")),
            "middle_exists": os.path.exists(os.path.join(output_dir, "middle.json")),
        }

        # 1) content_list 标准化
        content_list_files = []
        canonical_content = os.path.join(output_dir, "content_list.json")
        if os.path.exists(canonical_content):
            content_list_files.append(canonical_content)
        for name in os.listdir(output_dir):
            if name.endswith("_content_list.json"):
                content_list_files.append(os.path.join(output_dir, name))

        # 去重并按 mtime 降序，优先选择最新文件
        content_list_files = sorted(
            {os.path.abspath(p) for p in content_list_files},
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )

        if content_list_files:
            selected = content_list_files[0]
            # 将选中文件复制为 canonical content_list.json（不删除原始文件）
            if os.path.abspath(selected) != os.path.abspath(canonical_content):
                shutil.copy2(selected, canonical_content)
            selected_hash = self._file_md5(canonical_content)
            manifest["selected_content_list"] = os.path.basename(selected)
            manifest["selected_content_list_hash"] = selected_hash
            for p in content_list_files:
                try:
                    manifest["content_list_candidates"].append(
                        {
                            "file": os.path.basename(p),
                            "md5": self._file_md5(p),
                            "mtime": int(os.path.getmtime(p)),
                        }
                    )
                except Exception:
                    manifest["content_list_candidates"].append(
                        {"file": os.path.basename(p), "md5": "", "mtime": 0}
                    )

        # 2) middle.json 兼容回退
        middle_path = os.path.join(output_dir, "middle.json")
        model_path = os.path.join(output_dir, "model.json")
        if (not os.path.exists(middle_path)) and os.path.exists(model_path):
            shutil.copy2(model_path, middle_path)
        manifest["middle_exists"] = os.path.exists(middle_path)

        # 3) 写 manifest
        try:
            with open(
                os.path.join(output_dir, "extract_manifest.json"), "w", encoding="utf-8"
            ) as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[MinerU] 写入 extract_manifest.json 失败: {e}")

    def _submit_task(self, file_path):
        """
        步骤一：提交任务
        1. 申请上传链接
        2. 上传文件
        """
        apply_url = f"{self.base_url}/file-urls/batch"
        filename = os.path.basename(file_path)

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

            logger.info("[→] 链接申请成功，正在上传文件数据...")

            # 2. 上传文件 (使用标准 requests，不带额外 Header)
            with open(file_path, "rb") as f:
                file_content = f.read()

                # 直接 PUT 二进制，timeout 设置长一点
                resp_upload = std_requests.put(
                    upload_url, data=file_content, timeout=300
                )

            if resp_upload.status_code != 200:
                logger.error(f"[✗] 文件上传失败 HTTP {resp_upload.status_code}")
                logger.error(f"    详情: {resp_upload.text[:200]}")
                return None

            logger.info(f"[✓] 文件上传成功! Batch ID: {batch_id}")
            return batch_id

        except Exception as e:
            logger.error(f"[✗] 提交异常: {e}")
            return None

    def _wait_for_completion(self, batch_id, max_wait_time=600):
        """
        步骤二：等待任务完成
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

                # 提取结果列表
                results = data.get("extract_result", [])
                if not results:
                    results = data.get("count", [])

                if not results:
                    time.sleep(5)
                    continue

                # 取第一个文件的结果
                item = results[0]

                # 获取状态字段
                state = item.get("state") or item.get("status")

                # 判断状态值
                if state in ["done", "success"]:
                    # 获取下载链接
                    download_url = item.get("full_zip_url") or item.get("full_res")
                    logger.info("[✓] 解析成功")
                    return download_url

                elif state in ["failed", "error"]:
                    error_msg = item.get("error_msg", "未知错误")
                    logger.error(f"[✗] 解析失败: {error_msg}")
                    return None

                else:
                    # 仍在处理中
                    elapsed = int(time.time() - start_time)
                    if elapsed % 10 == 0:
                        logger.info(f"[⏳] 解析中... (当前状态: {state})")
                    time.sleep(5)

            except Exception as e:
                logger.warning(f"轮询异常: {e}")
                time.sleep(5)

        logger.error(f"[✗] 任务等待超时 ({max_wait_time}秒)")
        return None

    def _download_and_extract(self, result_url, output_dir):
        """
        步骤三：下载并解压结果
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

            # 扫描目录中的文件
            logger.info(f"📂 正在扫描目录 {output_dir} ...")
            file_list = []
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, output_dir)
                    file_list.append(rel_path)
                    logger.info(f"   📄 发现文件: {rel_path}")

            # 寻找解析结果文件（兼容新版 content_list）
            found_json = None

            for f in file_list:
                if f.endswith("middle.json"):
                    found_json = f
                    break
                # 兼容其他可能的命名
                if f.endswith("model.json"):
                    found_json = f
                if f.endswith("_content_list.json"):
                    found_json = f

            if found_json:
                # 仅对 middle/model 做 middle.json 归一；content_list 保留原名，后续 canonicalize
                src_path = os.path.join(output_dir, found_json)
                if found_json.endswith(("middle.json", "model.json")):
                    dst_path = os.path.join(output_dir, "middle.json")
                    if src_path != dst_path:
                        if os.path.exists(dst_path):
                            os.remove(dst_path)
                        shutil.move(src_path, dst_path)
                        logger.info(
                            f"[✓] 已将 {found_json} 移动并重命名为 middle.json"
                        )
                return True
            else:
                logger.warning(
                    "[!] 未找到 middle.json/model.json/content_list.json，请检查上方打印的文件列表"
                )
                return False

        except Exception as e:
            logger.error(f"下载解压失败: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(description="MinerU 统一文件提取 (PDF/DOC/DOCX)")
    parser.add_argument("--raw-data-dir", default="../data/", help="原始数据目录")
    parser.add_argument(
        "--result-dir", default="preprocess/extract_output", help="输出结果目录"
    )
    parser.add_argument("--doc-id", type=str, default=None, help="指定处理的文档 ID")
    args = parser.parse_args()

    os.makedirs(args.result_dir, exist_ok=True)
    extractor = MinerUExtractor()

    search_path = os.path.join(args.raw_data_dir, "*")
    doc_count = 0

    all_items = glob.glob(search_path)
    for item in all_items:
        if not os.path.isdir(item):
            continue
        sid = os.path.basename(item)
        if args.doc_id and sid != args.doc_id:
            continue

        # 查找待提取文件：优先 PDF，其次 DOCX/DOC（MinerU API 支持 pdf、doc、ppt、图片）
        doc_path = None
        pdf_list = glob.glob(os.path.join(item, "*.pdf"))
        if pdf_list:
            doc_path = pdf_list[0]
        elif os.path.exists(os.path.join(item, "document.pdf")):
            doc_path = os.path.join(item, "document.pdf")
        else:
            # 支持 docx/doc，走与 PDF 相同的 MinerU 链路
            for ext in ("*.docx", "*.doc"):
                docx_list = glob.glob(os.path.join(item, ext))
                if docx_list:
                    doc_path = docx_list[0]
                    break
        if not doc_path:
            continue

        doc_count += 1
        logger.info(f"\n{'='*60}")
        logger.info(f"[{doc_count}] 处理文档: {sid} -> {os.path.basename(doc_path)}")
        logger.info(f"{'='*60}")
        extractor.extract_file(doc_path, sid, args.result_dir)

    logger.info(f"\n[✓] 全部处理完成，共处理 {doc_count} 个文档")


if __name__ == "__main__":
    main()
